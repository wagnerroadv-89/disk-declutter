"""Limpeza de disco - servidor local (somente 127.0.0.1).

Fluxo: a pagina lista os discos -> voce marca quais varrer -> "Escanear agora" -> uma aba por disco.
O scan e SOMENTE LEITURA. Apagar/mover so acontece com clique + dupla confirmacao na pagina, e o
servidor so age sobre itens que ELE MESMO listou no scan (nunca aceita caminhos arbitrarios).
Todas as operacoes ficam registradas em limpeza.log.
"""
import os, sys, json, shutil, stat, ctypes, subprocess, threading, secrets, time, uuid, re, webbrowser, socket
import http.server, socketserver, urllib.parse
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scanner

LOG_FILE = os.environ.get("LIMPEZA_LOG", os.path.join(HERE, "limpeza.log"))
TEST_PICK = os.environ.get("LIMPEZA_TEST_PICK")          # so para testes automatizados
FIXTURE = os.environ.get("LIMPEZA_FIXTURE")              # so para testes: adiciona um disco falso "T:"
PORT = int(os.environ.get("LIMPEZA_PORT", "8765"))
TOKEN = secrets.token_urlsafe(24)
LOCK = threading.Lock()          # uma operacao de limpeza por vez

ITEMS = {}      # id -> item
STATE = {}      # id -> {"status","msg","freed"}
SCANS = {}      # "C:" -> {status, phase, files, found, started, finished, error}
JOBS = {}
ITEMS_LOCK = threading.Lock()

# Validade do scan: tudo fica SO em memoria. Se o painel ficar TTL segundos sem dar sinal de vida
# (aba fechada), o resultado e descartado e o painel volta ao estado inicial (novo scan obrigatorio).
TTL = float(os.environ.get("LIMPEZA_TTL_SEC", "600"))     # padrao: 10 minutos
LAST_SEEN = time.time()
EPOCH = 1


def touch():
    global LAST_SEEN
    LAST_SEEN = time.time()


def is_idle():
    return not LOCK.locked() and not any(s.get("status") == "running" for s in SCANS.values())


def reset_state(why):
    global EPOCH
    with ITEMS_LOCK:
        ITEMS.clear(); STATE.clear(); SCANS.clear(); JOBS.clear()
    EPOCH += 1
    log("Estado do painel descartado (%s). Próxima abertura exige novo scan." % why)


def expire_if_needed():
    if (SCANS or ITEMS) and is_idle() and time.time() - LAST_SEEN > TTL:
        reset_state("painel sem sinal de vida há %d s" % (time.time() - LAST_SEEN))


def janitor():
    while True:
        time.sleep(max(1.0, min(5.0, TTL / 4)))
        try:
            expire_if_needed()
        except Exception as e:
            log("janitor: %s" % e)


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def app_version():
    try:
        return open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return "?"


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%.0f %s" if u in ("B", "KB") else "%.1f %s") % (n, u)
        n /= 1024.0


# ---------------------------------------------------------------- discos
def list_drives():
    out = []
    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    sysd = os.environ.get("SystemDrive", "C:").upper()
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(65 + i) + ":"
        typ = k.GetDriveTypeW(letter + "\\")
        if typ not in (2, 3):          # 2 = removivel, 3 = fixo (ignora rede, CD)
            continue
        try:
            du = shutil.disk_usage(letter + "\\")
        except OSError:
            continue
        buf = ctypes.create_unicode_buffer(261)
        label = ""
        if k.GetVolumeInformationW(letter + "\\", buf, 261, None, None, None, None, 0):
            label = buf.value
        out.append({"letter": letter, "label": label, "total": du.total, "free": du.free,
                    "system": letter == sysd, "removable": typ == 2})
    if FIXTURE:
        out.append({"letter": "T:", "label": "TESTE (dados falsos)", "total": 10**9, "free": 5 * 10**8, "system": False, "removable": False})
    return out


def is_protected(path, trusted=False):
    n = norm(path)
    drv = os.path.splitdrive(n)[0]
    if len(n) <= 3:
        return True
    parts = n.split("\\")
    if n in (drv + "\\windows", drv + "\\program files", drv + "\\program files (x86)", drv + "\\programdata"):
        return True
    if not trusted and (n.startswith(drv + "\\windows\\") or n.startswith(drv + "\\$recycle.bin") or n.startswith(drv + "\\system volume information")):
        return True
    if len(parts) >= 2 and parts[1] == "users":
        if len(parts) <= 3:
            return True       # \users e \users\<nome>
        if len(parts) == 4 and parts[3] in ("appdata", "documents", "documentos", "desktop", "downloads", "pictures", "imagens",
                                           "videos", "vídeos", "music", "músicas", "onedrive"):
            return True
        if len(parts) == 5 and parts[3] == "appdata" and parts[4] in ("local", "roaming", "locallow"):
            return True
    return False


# ---------------------------------------------------------------- apagar
class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def to_recycle_bin(path):
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                       # FO_DELETE
    op.pFrom = path + "\0\0"
    op.fFlags = 0x40 | 0x10 | 0x400 | 0x4   # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if r != 0 or op.fAnyOperationsAborted:
        raise OSError("A Lixeira recusou o item (código %s). Talvez esteja em uso." % r)


def rm_permanent(path):
    """Apaga arquivo/pasta SEM seguir junctions/links. Retorna o numero de falhas."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0
    except OSError:
        return 1
    failed = 0
    is_link = bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    if stat.S_ISDIR(st.st_mode) and not is_link:
        try:
            with os.scandir(path) as it:
                children = [e.path for e in it]
        except OSError:
            return 1
        for c in children:
            failed += rm_permanent(c)
        try:
            os.rmdir(path)
        except OSError:
            failed += 1
    else:
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        try:
            if stat.S_ISDIR(st.st_mode):
                os.rmdir(path)      # junction / link de pasta
            else:
                os.remove(path)
        except OSError:
            failed += 1
    return failed


def clear_contents(path):
    failed = 0
    try:
        names = os.listdir(path)
    except OSError as e:
        raise OSError("Não foi possível ler a pasta: %s" % e)
    for n in names:
        failed += rm_permanent(os.path.join(path, n))
    return failed


def installed(key):
    import winreg
    sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, sub + "\\" + key, 0, winreg.KEY_READ | flag):
                return True
        except OSError:
            pass
    return False


def empty_recycle_bin():
    r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)   # sem confirmacao/progresso/som
    return r & 0xFFFFFFFF


def do_delete(it, recycle, jlog):
    """Retorna (bytes_liberados, status, mensagem)."""
    path = it["path"]; kind = it["kind"]
    if it.get("admin") and not is_admin():
        raise PermissionError("Requer executar como administrador (use iniciar-admin.bat).")
    if kind == "programa":
        cmd = it.get("uninstall") or ""
        if not cmd:
            raise RuntimeError("Este programa não informa comando de desinstalação.")
        cmd = re.sub(r"(?i)(msiexec(?:\.exe)?\s+)/I", r"\1/X", cmd)
        jlog("Abrindo o desinstalador (confirme na janela que aparecer): " + cmd)
        rc = subprocess.run(cmd, shell=True).returncode
        if it.get("steam"):
            return 0, "info", "Pedido enviado ao Steam: confirme a desinstalação na janela do Steam."
        if it.get("key") and installed(it["key"]):
            raise RuntimeError("O programa continua instalado (código %s). Desinstalação cancelada ou incompleta." % rc)
        return it["size"], "ok", "Desinstalado"
    if kind == "sistema":
        r = subprocess.run(it["action"], shell=True, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("Falhou: " + (r.stderr or r.stdout).strip())
        return it["size"], "ok", "Concluído"
    if not os.path.exists(path):
        raise FileNotFoundError("Já não existe.")
    if is_protected(path, it.get("trusted", False)):
        raise PermissionError("Caminho protegido, recusado.")
    direct = it["cat"].startswith("Cache") or kind == "conteudo"
    if kind == "conteudo":
        failed = clear_contents(path)
        if failed:
            raise RuntimeError("Limpo parcialmente: %d item(ns) em uso foram ignorados." % failed)
        return it["size"], "ok", "Limpo"
    if recycle and not direct:
        to_recycle_bin(path)
    else:
        failed = rm_permanent(path)
        if failed:
            raise RuntimeError("Apagado parcialmente: %d arquivo(s) em uso." % failed)
    return it["size"], "ok", "Apagado"


# ---------------------------------------------------------------- mover
def tree_stats(path):
    n = 0; s = 0
    if os.path.isfile(path):
        return 1, os.path.getsize(path)
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                s += os.path.getsize(os.path.join(root, f)); n += 1
            except OSError:
                pass
    return n, s


def do_move(it, dest, junction, jlog):
    src = it["path"]
    if not it.get("movable"):
        raise PermissionError("Este item não pode ser movido.")
    if not os.path.exists(src):
        raise FileNotFoundError("Já não existe.")
    if is_protected(src, it.get("trusted", False)):
        raise PermissionError("Caminho protegido, recusado.")
    if not os.path.isdir(dest):
        raise FileNotFoundError("Pasta destino não existe: " + dest)
    ns, ss = norm(src), norm(dest)
    if ss == ns or ss.startswith(ns + os.sep):
        raise ValueError("O destino não pode estar dentro da própria pasta de origem.")
    if os.path.splitdrive(src)[0].upper() == os.path.splitdrive(dest)[0].upper():
        raise ValueError("O destino está no mesmo disco; não libera espaço. Escolha outro disco.")
    n0, s0 = tree_stats(src)
    free = shutil.disk_usage(dest).free
    if free < s0 * 1.02 + 50 * 2**20:
        raise OSError("Espaço insuficiente no destino (livre %s, precisa %s)." % (human(free), human(s0)))
    target = os.path.join(dest, os.path.basename(src))
    k = 2
    while os.path.exists(target):
        base, ext = os.path.splitext(os.path.basename(src)) if os.path.isfile(src) else (os.path.basename(src), "")
        target = os.path.join(dest, "%s (%d)%s" % (base, k, ext)); k += 1
    jlog("Copiando %s (%s) para %s ..." % (src, human(s0), target))
    try:
        if os.path.isdir(src):
            shutil.copytree(src, target, symlinks=True)
        else:
            shutil.copy2(src, target)
    except Exception:
        jlog("Falha ao copiar; removendo cópia parcial do destino.")
        if os.path.exists(target):
            rm_permanent(target)
        raise
    n1, s1 = tree_stats(target)
    if (n0, s0) != (n1, s1):
        rm_permanent(target)
        raise OSError("Verificação falhou (origem %d arq/%s; cópia %d arq/%s). Origem mantida." % (n0, human(s0), n1, human(s1)))
    jlog("Cópia verificada (%d arquivos, %s). Removendo a origem ..." % (n1, human(s1)))
    failed = rm_permanent(src)
    if failed:
        raise RuntimeError("Copiado para %s, mas %d arquivo(s) da origem estão em uso e não foram removidos." % (target, failed))
    if junction and os.path.isdir(target):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", src, target], capture_output=True, text=True)
        if r.returncode != 0:
            jlog("Aviso: não consegui criar o atalho (junction): " + (r.stdout + r.stderr).strip())
            return s0, target + " (sem atalho no local antigo)"
        jlog("Atalho (junction) criado em %s -> %s" % (src, target))
    return s0, target


# ---------------------------------------------------------------- jobs de limpeza
def drives_free():
    return {d["letter"]: {"free": d["free"], "total": d["total"]} for d in list_drives()}


def run_job(job, plan, empty_bin=False):
    def jlog(m):
        job["log"].append(m); log(m)
    freed_total = 0
    try:
        for kind, it, arg in plan:
            iid = it["id"]
            job["current"] = iid
            try:
                if kind == "delete":
                    jlog("%s: %s" % ("Desinstalando" if it["kind"] == "programa" else "Apagando", it["name"]))
                    freed, status, msg = do_delete(it, arg["recycle"], jlog)
                    res = {"status": status, "msg": msg, "freed": freed}
                else:
                    jlog("Movendo: %s" % it["name"])
                    freed, target = do_move(it, arg["dest"], arg["junction"], jlog)
                    res = {"status": "ok", "msg": "Movido para " + target, "freed": freed}
                job["results"][iid] = res
                if res["status"] == "ok":
                    STATE[iid] = res
                freed_total += freed
                jlog("OK: %s" % it["name"])
            except Exception as e:
                job["results"][iid] = {"status": "erro", "msg": str(e), "freed": 0}
                jlog("ERRO em %s -> %s: %s" % (it["name"], type(e).__name__, e))
        if empty_bin:
            if any(k == "delete" for k, _, _ in plan):
                jlog("Esvaziando a Lixeira do Windows ...")
                code = empty_recycle_bin()
                jlog("Lixeira esvaziada." if code == 0 else "Aviso: não consegui esvaziar a Lixeira (código 0x%X)." % code)
            else:
                jlog("Esvaziar a Lixeira ignorado: esta operação não apagou nada.")
    finally:
        job["current"] = None
        job["done"] = True
        job["freed"] = freed_total
        jlog("Concluído. Espaço liberado (estimado): %s" % human(freed_total))


def pick_folder():
    if TEST_PICK:
        return TEST_PICK
    code = ("import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)\n"
            "p=filedialog.askdirectory(title='Escolha a pasta de destino (para onde mover)', mustexist=True)\n"
            "print(p or '')")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True).stdout.strip()
    return os.path.normpath(out) if out else ""


# ---------------------------------------------------------------- scan
def scan_thread(letter):
    st = SCANS[letter]
    try:
        if letter == "T:" and FIXTURE:
            items = json.load(open(FIXTURE, encoding="utf-8"))["items"]
            for it in items:
                it["drive"] = "T:"
            st.update(phase="Lendo dados de teste…", files=0, found=len(items))
        else:
            items = scanner.scan_drive(letter, st)
        with ITEMS_LOCK:
            for it in items:
                ITEMS[it["id"]] = it
            st["ids"] = [it["id"] for it in items]
        st["status"] = "done"
        st["phase"] = "Concluído"
        log("Scan de %s concluído: %d itens, %s" % (letter, len(items), human(sum(i["size"] for i in items))))
    except Exception as e:
        st["status"] = "error"; st["error"] = "%s: %s" % (type(e).__name__, e)
        log("Scan de %s falhou: %s" % (letter, st["error"]))
    finally:
        st["finished"] = time.time()


def start_scans(letters):
    valid = {d["letter"] for d in list_drives()}
    started = []
    for l in letters:
        l = l.upper()
        if l not in valid:
            raise ValueError("Disco inválido: %s" % l)
        if SCANS.get(l, {}).get("status") == "running":
            continue
        with ITEMS_LOCK:
            for iid in SCANS.get(l, {}).get("ids", []):
                ITEMS.pop(iid, None); STATE.pop(iid, None)
        SCANS[l] = {"status": "running", "phase": "Iniciando…", "files": 0, "found": 0, "started": time.time(), "ids": []}
        threading.Thread(target=scan_thread, args=(l,), daemon=True).start()
        started.append(l)
        log("Scan iniciado: %s" % l)
    return started


def public_state():
    with ITEMS_LOCK:
        items = [it for it in ITEMS.values()]
    scans = {k: {x: v for x, v in s.items() if x != "ids"} for k, s in SCANS.items()}
    return {"drives": list_drives(), "scans": scans, "items": items, "state": STATE, "admin": is_admin(),
            "epoch": EPOCH, "ttl": TTL, "now": time.time()}


# ---------------------------------------------------------------- HTTP
class H(http.server.BaseHTTPRequestHandler):
    server_version = "LimpezaDisco/2.0"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        h = (self.headers.get("Host") or "").lower()
        return h in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"erro": "host inválido"})
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            expire_if_needed()      # abriu o painel depois do prazo: comeca do zero
            touch()
            html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/info":
            # sem token e sem mexer na validade do scan: so diz se e este app e em que modo roda
            # (usado pela skill para decidir se reaproveita o servidor ou reinicia no modo escolhido)
            return self._send(200, {"app": "limpeza-de-disco", "admin": is_admin(), "version": app_version()})
        if self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"erro": "token inválido"})
        touch()
        if u.path == "/api/ping":
            return self._send(200, {"epoch": EPOCH, "ttl": TTL})
        if u.path == "/api/state":
            return self._send(200, public_state())
        if u.path == "/api/job":
            jid = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            j = JOBS.get(jid)
            if not j:
                return self._send(404, {"erro": "job não encontrado"})
            return self._send(200, dict(j, drives=drives_free()))
        self._send(404, {"erro": "não encontrado"})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"erro": "acesso negado"})
        touch()
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"erro": "JSON inválido"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/pick-folder":
            p = pick_folder()
            if not p:
                return self._send(200, {"path": ""})
            if not os.path.isdir(p):
                return self._send(400, {"erro": "Pasta inválida"})
            return self._send(200, {"path": p, "free": shutil.disk_usage(p).free})
        if path == "/api/scan":
            if LOCK.locked():
                return self._send(409, {"erro": "Há uma limpeza em andamento. Aguarde terminar."})
            letters = body.get("drives") or []
            if not isinstance(letters, list) or not letters:
                return self._send(400, {"erro": "Marque pelo menos um disco."})
            try:
                return self._send(200, {"started": start_scans([str(x) for x in letters])})
            except ValueError as e:
                return self._send(400, {"erro": str(e)})
        if path == "/api/run":
            return self._run(body)
        self._send(404, {"erro": "não encontrado"})

    def _run(self, b):
        action = b.get("action")
        recycle = bool(b.get("recycle", True)); junction = bool(b.get("junction", True))
        empty_bin = bool(b.get("emptyBin", False))
        if empty_bin:
            recycle = False   # esvaziar a Lixeira depois torna inutil enviar para ela
        dels = list(b.get("delete") or []); moves = dict(b.get("moves") or {})
        if any(s.get("status") == "running" for s in SCANS.values()):
            return self._send(409, {"erro": "Há um scan em andamento. Aguarde terminar."})
        if action == "delete_all":
            if b.get("confirmText") != "APAGAR TUDO":
                return self._send(400, {"erro": "Confirmação 'APAGAR TUDO' ausente."})
            drv = str(b.get("drive") or "").upper()
            dels = [i["id"] for i in ITEMS.values() if i["drive"] == drv and i["risk"] != "alto"
                    and STATE.get(i["id"], {}).get("status") != "ok" and not (i.get("admin") and not is_admin())]
            moves = {}
        elif action not in ("delete", "move", "both"):
            return self._send(400, {"erro": "ação inválida"})
        if action == "delete": moves = {}
        if action == "move": dels = []
        if not b.get("confirm") or not b.get("confirm2"):
            return self._send(400, {"erro": "Falta a dupla confirmação."})
        bad = [x for x in dels + list(moves) if x not in ITEMS]
        if bad:
            return self._send(400, {"erro": "item desconhecido"})
        if set(dels) & set(moves):
            return self._send(400, {"erro": "Um item não pode ser apagado e movido ao mesmo tempo."})
        for iid, d in moves.items():
            if not ITEMS[iid].get("movable"):
                return self._send(400, {"erro": "'%s' não pode ser movido." % ITEMS[iid]["name"]})
            if not isinstance(d, str) or not os.path.isdir(d):
                return self._send(400, {"erro": "Destino inválido para '%s'." % ITEMS[iid]["name"]})
        if not dels and not moves:
            return self._send(400, {"erro": "Nada a fazer."})
        if not LOCK.acquire(blocking=False):
            return self._send(409, {"erro": "Já existe uma operação em andamento."})
        plan = [("delete", ITEMS[i], {"recycle": recycle}) for i in dels]
        plan += [("move", ITEMS[i], {"dest": d, "junction": junction}) for i, d in moves.items()]
        jid = uuid.uuid4().hex[:10]
        job = JOBS[jid] = {"id": jid, "log": [], "results": {}, "done": False, "current": None, "total": len(plan), "freed": 0}
        log("=== Nova operação %s: %s | apagar=%d mover=%d lixeira=%s esvaziar_lixeira=%s ===" % (jid, action, len(dels), len(moves), recycle, empty_bin))

        def runner():
            try:
                run_job(job, plan, empty_bin)
            finally:
                LOCK.release()
        threading.Thread(target=runner, daemon=True).start()
        self._send(200, {"job": jid})


class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def free_port(start):
    for p in range(start, start + 11):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("Nenhuma porta livre entre %d e %d." % (start, start + 10))


if __name__ == "__main__":
    PORT = free_port(PORT)
    srv = Srv(("127.0.0.1", PORT), H)
    url = "http://127.0.0.1:%d/" % PORT
    try:
        open(os.path.join(HERE, "server.url"), "w").write(url)
    except OSError:
        pass
    print("Limpeza de disco rodando em", url, "(administrador: %s)" % ("sim" if is_admin() else "não"))
    print("Feche esta janela para encerrar. Nada é apagado até você confirmar na página.")
    print("O resultado do scan fica só na memória e é descartado %d min depois que o painel for fechado." % round(TTL / 60) if TTL >= 60
          else "O resultado do scan é descartado %d s depois que o painel for fechado." % TTL, flush=True)
    threading.Thread(target=janitor, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
