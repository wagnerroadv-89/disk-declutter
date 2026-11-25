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
_TEST_PICK_N = [0]
TEST_FAIL_RECYCLE = os.environ.get("LIMPEZA_TEST_FAIL_RECYCLE")    # so para testes: finge que a Lixeira recusou o item
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
        ITEMS.clear(); STATE.clear(); SCANS.clear(); JOBS.clear(); CTXS.clear()
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


class JobAborted(Exception):
    """O usuario clicou em "Abortar operacao"."""


def rm_permanent(path, ctx=None, fails=None, abortable=True):
    """Apaga arquivo/pasta SEM seguir junctions/links. Retorna o numero de falhas; `fails` recebe (caminho, erro).
    Com `ctx`, conta os bytes apagados (progresso) e, se `abortable`, confere o botao "Abortar" a cada item."""
    if ctx and abortable:
        ctx.check()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0
    except OSError as e:
        if fails is not None:
            fails.append((path, str(e)))
        return 1
    failed = 0
    is_link = bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    if stat.S_ISDIR(st.st_mode) and not is_link:
        try:
            with os.scandir(path) as it:
                children = [e.path for e in it]
        except OSError as e:
            if fails is not None:
                fails.append((path, str(e)))
            return 1
        for c in children:
            failed += rm_permanent(c, ctx, fails, abortable)
        try:
            os.rmdir(path)
        except OSError as e:
            failed += 1
            if fails is not None:
                fails.append((path, str(e)))
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
                if ctx:
                    ctx.add_bytes(st.st_size)       # progresso: bytes ja apagados
        except OSError as e:
            failed += 1
            if fails is not None:
                fails.append((path, str(e)))
    return failed


def clear_contents(path, ctx=None, fails=None):
    failed = 0
    try:
        names = os.listdir(path)
    except OSError as e:
        raise OSError("Não foi possível ler a pasta: %s" % e)
    for n in names:
        failed += rm_permanent(os.path.join(path, n), ctx, fails)
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


def take_ownership(path, ctx):
    """Administrador: assume a propriedade e concede controle total aos Administradores (takeown + icacls)."""
    isdir = os.path.isdir(path)
    ok = False
    for ans in ("Y", "S"):                      # o takeown pede Y/N (ingles) ou S/N (portugues) conforme o idioma do Windows
        cmd = ["takeown", "/f", path] + (["/r", "/d", ans] if isdir else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            ok = True
            break
    cmd = ["icacls", path, "/grant", "*S-1-5-32-544:(OI)(CI)F", "/c", "/q"] + (["/t"] if isdir else [])
    r2 = subprocess.run(cmd, capture_output=True, text=True)
    ctx.log("Propriedade/permissão ajustadas em %s (takeown %s, icacls %s)." % (path, "ok" if ok else "falhou", "ok" if r2.returncode == 0 else "falhou"))


def remove_with_policy(it, ctx, remove):
    """remove(fails) -> numero de falhas. Se algo nao puder ser apagado (em uso / acesso negado), PAUSA e pergunta ao usuario:
    tentar de novo, assumir a propriedade (so administrador) ou pular. Retorna o numero de falhas que restou."""
    failed = 0
    for _ in range(4):
        fails = []
        failed = remove(fails)
        if not failed:
            return 0
        sample = "; ".join("%s (%s)" % (os.path.basename(p) or p, e.split("]")[-1].strip()[:60]) for p, e in fails[:3])
        opts = [("retry", "Tentar de novo (feche antes o programa que está usando estes arquivos)")]
        if is_admin():
            opts.append(("own", "Assumir a propriedade e apagar (administrador)"))
        opts.append(("skip", "Pular — deixar o que sobrou"))
        ch = ctx.ask(it, "locked", "%d arquivo(s) de “%s” não puderam ser apagados (em uso ou acesso negado). Ex.: %s" % (failed, it["name"], sample), opts)
        if ch == "own":
            take_ownership(it["path"], ctx)
        elif ch != "retry":
            return failed
    return failed


def do_delete(it, recycle, ctx):
    """Retorna (bytes_liberados, status, mensagem). status: ok | info | parcial | pulado."""
    path = it["path"]; kind = it["kind"]
    if it.get("admin") and not is_admin():
        raise PermissionError("Requer executar como administrador (use iniciar-admin.bat).")
    if kind == "programa":
        cmd = it.get("uninstall") or ""
        if not cmd:
            raise RuntimeError("Este programa não informa comando de desinstalação.")
        cmd = re.sub(r"(?i)(msiexec(?:\.exe)?\s+)/I", r"\1/X", cmd)
        ctx.phase("Aguardando o desinstalador (confirme na janela dele)")
        ctx.log("Abrindo o desinstalador (confirme na janela que aparecer): " + cmd)
        rc = subprocess.run(cmd, shell=True).returncode
        if it.get("steam"):
            return 0, "info", "Pedido enviado ao Steam: confirme a desinstalação na janela do Steam."
        if it.get("key") and installed(it["key"]):
            if rc == 740 and not is_admin():
                raise RuntimeError("Este desinstalador exige ADMINISTRADOR (código 740). Feche o painel, abra com "
                                   "iniciar-admin.bat, escaneie de novo e tente outra vez.")
            hint = (" Se você cancelou a janela do desinstalador, é isso mesmo." if rc in (0, 1, 2) else "") + \
                   ("" if is_admin() or not it.get("uninstall_admin") else " Este programa costuma exigir administrador: tente pelo iniciar-admin.bat.")
            raise RuntimeError("O programa continua instalado (código %s).%s" % (rc, hint))
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
    ctx.phase("Apagando", copy_size=it["size"])
    if kind == "conteudo":
        failed = remove_with_policy(it, ctx, lambda f: clear_contents(path, ctx, f))
        if failed:
            return 0, "parcial", "Limpo parcialmente: %d item(ns) ficaram (em uso ou acesso negado)." % failed
        return it["size"], "ok", "Limpo"
    if recycle and not direct:
        try:
            if TEST_FAIL_RECYCLE:
                raise OSError("falha simulada para teste")
            to_recycle_bin(path)
            return it["size"], "ok", "Apagado"
        except OSError as e:
            ch = ctx.ask(it, "recycle", "O Windows recusou enviar “%s” para a Lixeira (%s). Quer apagar DEFINITIVAMENTE, sem passar pela Lixeira?" % (it["name"], e),
                         [("perm", "Apagar definitivamente (sem Lixeira)"), ("skip", "Pular este item")])
            if ch != "perm":
                return 0, "pulado", "Pulado: a Lixeira recusou o item e você preferiu não apagar definitivamente."
    failed = remove_with_policy(it, ctx, lambda f: rm_permanent(path, ctx, f))
    if failed:
        return 0, "parcial", "Apagado parcialmente: %d arquivo(s) ficaram (em uso ou acesso negado)." % failed
    return it["size"], "ok", "Apagado"


# ---------------------------------------------------------------- mover
def tree_stats(path, ctx=None):
    n = 0; s = 0
    if os.path.isfile(path):
        return 1, os.path.getsize(path)
    for root, dirs, files in os.walk(path):
        if ctx:
            ctx.check()
        for f in files:
            try:
                s += os.path.getsize(os.path.join(root, f)); n += 1
            except OSError:
                pass
    return n, s


def copy_tree(src, dst, ctx):
    """Copia arquivo ou pasta reportando bytes copiados (progresso) e respeitando "Abortar" a cada bloco de 8 MB."""
    done = [0]

    def cp_file(s, d):
        with open(s, "rb") as fi, open(d, "wb") as fo:
            while True:
                ctx.check()
                buf = fi.read(8 * 1024 * 1024)
                if not buf:
                    break
                fo.write(buf)
                done[0] += len(buf)
                ctx.progress(done[0])
        try:
            shutil.copystat(s, d)
        except OSError:
            pass

    def walk(s, d):
        os.makedirs(d, exist_ok=True)
        with os.scandir(s) as it:
            entries = list(it)
        for e in entries:
            ctx.check()
            sp = e.path; dp = os.path.join(d, e.name)
            if e.is_symlink():
                try:
                    os.symlink(os.readlink(sp), dp, target_is_directory=e.is_dir())
                except OSError:
                    pass                # link simbolico que nao da para recriar: ignorado
            elif e.is_dir():
                walk(sp, dp)
            else:
                cp_file(sp, dp)
        try:
            shutil.copystat(s, d)
        except OSError:
            pass
    if os.path.isdir(src):
        walk(src, dst)
    else:
        cp_file(src, dst)


def do_move(it, dest, junction, ctx):
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
    ctx.phase("Medindo a origem…")
    n0, s0 = tree_stats(src, ctx)
    free = shutil.disk_usage(dest).free
    if free < s0 * 1.02 + 50 * 2**20:
        raise OSError("Espaço insuficiente no destino (livre %s, precisa %s)." % (human(free), human(s0)))
    target = os.path.join(dest, os.path.basename(src))
    k = 2
    while os.path.exists(target):
        base, ext = os.path.splitext(os.path.basename(src)) if os.path.isfile(src) else (os.path.basename(src), "")
        target = os.path.join(dest, "%s (%d)%s" % (base, k, ext)); k += 1
    ctx.log("Copiando %s (%s) para %s ..." % (src, human(s0), target))
    ctx.phase("Copiando", copy_size=s0, base=0.0, span=0.9)
    try:
        copy_tree(src, target, ctx)
        ctx.phase("Verificando a cópia…")
        n1, s1 = tree_stats(target, ctx)
    except BaseException as e:          # abortado, erro de leitura/escrita, etc.: a origem NAO foi tocada
        ctx.log("Cópia interrompida (%s); removendo a cópia parcial do destino. A origem continua intacta." % type(e).__name__)
        if os.path.exists(target):
            rm_permanent(target)
        raise
    if (n0, s0) != (n1, s1):
        rm_permanent(target)
        raise OSError("Verificação falhou (origem %d arq/%s; cópia %d arq/%s). Origem mantida." % (n0, human(s0), n1, human(s1)))
    ctx.log("Cópia verificada (%d arquivos, %s). Removendo a origem ..." % (n1, human(s1)))
    ctx.phase("Removendo a origem (etapa não interrompível)", copy_size=s0, base=0.9, span=0.1)
    failed = remove_with_policy(it, ctx, lambda f: rm_permanent(src, ctx, f, False))
    if failed:
        raise RuntimeError("Copiado para %s, mas %d arquivo(s) da origem não foram removidos (em uso ou acesso negado)." % (target, failed))
    if junction and os.path.isdir(target):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", src, target], capture_output=True, text=True)
        if r.returncode != 0:
            ctx.log("Aviso: não consegui criar o atalho (junction): " + (r.stdout + r.stderr).strip())
            return s0, target + " (sem atalho no local antigo)"
        ctx.log("Atalho (junction) criado em %s -> %s" % (src, target))
    return s0, target


# ---------------------------------------------------------------- jobs de limpeza
def drives_free():
    return {d["letter"]: {"free": d["free"], "total": d["total"]} for d in list_drives()}


CTXS = {}    # job id -> JobCtx


class JobCtx:
    """Contexto de um job: log, progresso, botao Abortar e perguntas ao usuario (politica de permissoes)."""

    def __init__(self, job):
        self.job = job
        self.abort = threading.Event()
        self.answered = threading.Event()
        self.answer = None
        self.remember = {}          # tipo de pergunta -> escolha ("aplicar a todos os proximos casos iguais")

    def log(self, m):
        self.job["log"].append(m); log(m)

    def check(self):
        if self.abort.is_set():
            raise JobAborted()

    def phase(self, text, copy_size=None, base=0.0, span=1.0):
        """base/span: que fatia (0..1) do item esta etapa representa na barra (ex.: copiar = 0..0.9, remover a origem = 0.9..1)."""
        cur = self.job.get("cur")
        if cur is not None:
            cur["phase"] = text
            if copy_size is not None:
                cur["copy_size"] = copy_size; cur["t_copy"] = time.time(); cur["base"] = base; cur["span"] = span
        self.job["cur_done"] = 0

    def progress(self, done):
        self.job["cur_done"] = done

    def add_bytes(self, n):
        self.job["cur_done"] = self.job.get("cur_done", 0) + n

    def ask(self, it, kind, msg, options, allow_all=True, timeout=1800):
        """Pausa o job e pergunta ao usuario na pagina. options = [(id, rotulo)]. Retorna o id escolhido."""
        if kind in self.remember:
            return self.remember[kind]
        pid = uuid.uuid4().hex[:8]
        self.answered.clear(); self.answer = None
        self.job["prompt"] = {"pid": pid, "item": it["name"], "kind": kind, "msg": msg, "allow_all": allow_all,
                              "options": [{"id": i, "label": l} for i, l in options]}
        self.log("PERGUNTA (%s): %s" % (it["name"], msg))
        t_end = time.time() + timeout
        try:
            while not self.answered.wait(0.4):
                self.check()
                if time.time() > t_end:
                    self.log("Sem resposta em %d min; item pulado." % (timeout // 60))
                    return "skip" if any(i == "skip" for i, _ in options) else options[-1][0]
        finally:
            self.job["prompt"] = None
        choice, all_ = self.answer
        if all_ and allow_all:
            self.remember[kind] = choice
        self.log("Resposta: %s%s" % (choice, " (para todos os próximos casos iguais)" if all_ else ""))
        return choice

    def decide(self, pid, choice, all_):
        p = self.job.get("prompt")
        if not p or p["pid"] != pid or choice not in [o["id"] for o in p["options"]]:
            return False
        self.answer = (choice, bool(all_)); self.answered.set()
        return True


def run_job(job, ctx, plan, empty_bin=False):
    freed_total = 0
    job["w_total"] = sum(max(it["size"], 2**20) for _, it, _ in plan)
    try:
        for idx, (kind, it, arg) in enumerate(plan):
            iid = it["id"]
            if ctx.abort.is_set():                     # abortado entre itens: o resto nem comeca
                for _, rest, _ in plan[idx:]:
                    job["results"][rest["id"]] = {"status": "cancelado", "msg": "Cancelado antes de começar.", "freed": 0}
                break
            w = max(it["size"], 2**20)
            job["current"] = iid
            verb = "Movendo" if kind == "move" else ("Desinstalando" if it["kind"] == "programa" else "Apagando")
            job["cur"] = {"name": it["name"], "verb": verb, "phase": "Iniciando…", "size": it["size"], "w": w, "copy_size": None, "t_copy": None}
            job["cur_done"] = 0
            try:
                if kind == "delete":
                    ctx.log("%s: %s" % ("Desinstalando" if it["kind"] == "programa" else "Apagando", it["name"]))
                    freed, status, msg = do_delete(it, arg["recycle"], ctx)
                    res = {"status": status, "msg": msg, "freed": freed}
                else:
                    ctx.log("Movendo: %s" % it["name"])
                    freed, target = do_move(it, arg["dest"], arg["junction"], ctx)
                    res = {"status": "ok", "msg": "Movido para " + target, "freed": freed}
                job["results"][iid] = res
                if res["status"] == "ok":
                    STATE[iid] = res
                freed_total += freed
                ctx.log("%s: %s" % ({"ok": "OK", "info": "INFO", "parcial": "PARCIAL", "pulado": "PULADO"}.get(res["status"], res["status"]), it["name"]))
            except JobAborted:
                job["pct_frozen"] = job_view(job)["pct"]       # a barra fica onde parou
                job["results"][iid] = {"status": "cancelado", "msg": "Cancelado durante a operação (o que já tinha sido apagado não volta; a origem de um 'mover' não é tocada).", "freed": 0}
                ctx.log("CANCELADO em %s" % it["name"])
                for _, rest, _ in plan[idx + 1:]:
                    job["results"][rest["id"]] = {"status": "cancelado", "msg": "Cancelado antes de começar.", "freed": 0}
                break
            except Exception as e:
                job["results"][iid] = {"status": "erro", "msg": str(e), "freed": 0}
                ctx.log("ERRO em %s -> %s: %s" % (it["name"], type(e).__name__, e))
            job["w_done"] += w
            job["items_done"] += 1
        job["aborted"] = ctx.abort.is_set()
        if empty_bin and not job["aborted"]:
            if any(k == "delete" and it["kind"] != "programa" for k, it, _ in plan):     # so se apagou arquivos (nao so desinstalou)
                ctx.log("Esvaziando a Lixeira do Windows ...")
                code = empty_recycle_bin()
                ctx.log("Lixeira esvaziada." if code == 0 else "Aviso: não consegui esvaziar a Lixeira (código 0x%X)." % code)
            else:
                ctx.log("Esvaziar a Lixeira ignorado: esta operação não apagou nada.")
    finally:
        job["current"] = None; job["cur"] = None; job["prompt"] = None
        job["done"] = True
        job["freed"] = freed_total
        ctx.log("%s. Espaço liberado (estimado): %s" % ("Operação ABORTADA" if job.get("aborted") else "Concluído", human(freed_total)))


def job_view(j):
    """Copia do job com campos calculados: percentual, velocidade e tempo restante da copia atual."""
    v = dict(j)
    cur = j.get("cur")
    part = 0.0
    if cur and cur.get("copy_size"):
        frac = min(1.0, j.get("cur_done", 0) / max(cur["copy_size"], 1))
        part = (cur.get("base", 0.0) + cur.get("span", 1.0) * frac) * cur["w"]
        el = time.time() - (cur.get("t_copy") or time.time())
        if el > 2 and j.get("cur_done", 0) > 0:
            spd = j["cur_done"] / el
            v["speed"] = spd
            v["eta"] = max(0.0, (cur["copy_size"] - j["cur_done"]) / spd)
    total = max(j.get("w_total", 1), 1)
    v["bytes_done"] = j["w_done"] + part          # progresso do job inteiro, em bytes (itens pequenos contam >= 1 MB)
    v["bytes_total"] = total
    v["pct"] = 100.0 if (j["done"] and not j.get("aborted")) else min(99.9, 100.0 * (j["w_done"] + part) / total)
    if j["done"] and j.get("aborted"):
        v["pct"] = j.get("pct_frozen", v["pct"])
    if v.get("speed"):
        v["eta_total"] = max(0.0, (total - v["bytes_done"]) / v["speed"])      # tempo estimado para concluir TUDO
    v["elapsed"] = time.time() - j["t0"]
    return v


def pick_folder():
    if TEST_PICK:                      # teste: "a|b|c" devolve a, b, c... em sequencia (e repete o ultimo)
        parts = TEST_PICK.split("|")
        _TEST_PICK_N[0] += 1
        return parts[min(_TEST_PICK_N[0], len(parts)) - 1]
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
            "epoch": EPOCH, "ttl": TTL, "now": time.time(),
            "active_job": next((j["id"] for j in JOBS.values() if not j["done"]), None)}


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
            return self._send(200, dict(job_view(j), drives=drives_free()))
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
        if path == "/api/abort":                     # botao "Abortar operacao"
            ctx = CTXS.get(str(body.get("job") or ""))
            if not ctx or ctx.job.get("done"):
                return self._send(404, {"erro": "Nenhuma operação em andamento."})
            ctx.abort.set(); ctx.job["aborting"] = True
            ctx.log("ABORTAR solicitado pelo usuário.")
            return self._send(200, {"ok": True})
        if path == "/api/decision":                  # resposta a uma pergunta de permissao
            ctx = CTXS.get(str(body.get("job") or ""))
            if not ctx or not ctx.decide(str(body.get("pid") or ""), str(body.get("choice") or ""), body.get("all")):
                return self._send(400, {"erro": "Pergunta inválida ou já respondida."})
            return self._send(200, {"ok": True})
        if path == "/api/reveal":
            return self._reveal(body)
        if path == "/api/run":
            return self._run(body)
        self._send(404, {"erro": "não encontrado"})

    def _reveal(self, b):
        """Abre o Explorer mostrando o item (seleciona o arquivo/pasta; em caches abre a propria pasta).
        So aceita o ID de um item listado pelo scan; nao altera nada."""
        it = ITEMS.get(str(b.get("id") or ""))
        if not it or not it.get("path"):
            return self._send(404, {"erro": "Item sem caminho para mostrar."})
        p = os.path.normpath(it["path"])
        if not os.path.exists(p):
            return self._send(404, {"erro": "Este caminho não existe mais (já foi apagado ou movido?)."})
        if it["kind"] == "conteudo" and os.path.isdir(p):
            cmd = 'explorer.exe "%s"' % p                  # cache/temporarios: abre dentro da pasta
        else:
            cmd = 'explorer.exe /select,"%s"' % p          # seleciona o item na pasta que o contem
        try:
            subprocess.Popen(cmd)        # string (nao lista) para o Explorer receber /select,"caminho" exatamente assim
        except OSError as e:
            return self._send(500, {"erro": "Não consegui abrir o Explorer: %s" % e})
        log("Explorer aberto em: %s" % p)
        self._send(200, {"ok": True})

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
            # scope: "files" (arquivos/pastas/caches), "programs" (so desinstalar) ou "both"; cada um exige um texto proprio
            scope = b.get("scope") or "files"
            need = {"files": "APAGAR TUDO", "programs": "DESINSTALAR TUDO", "both": "APAGAR E DESINSTALAR TUDO"}.get(scope)
            if need is None:
                return self._send(400, {"erro": "escopo inválido"})
            if b.get("confirmText") != need:
                return self._send(400, {"erro": "Confirmação '%s' ausente." % need})
            drv = str(b.get("drive") or "").upper()

            def in_scope(i):
                is_prog = i["kind"] == "programa"
                return (scope == "both") or (scope == "programs" and is_prog) or (scope == "files" and not is_prog)
            cand = [i for i in ITEMS.values() if i["drive"] == drv and i["risk"] != "alto" and in_scope(i)
                    and STATE.get(i["id"], {}).get("status") != "ok" and not (i.get("admin") and not is_admin())]
            cand.sort(key=lambda i: i["kind"] == "programa")      # arquivos primeiro, desinstaladores (interativos) por ultimo
            dels = [i["id"] for i in cand]
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
        dels.sort(key=lambda x: ITEMS[x]["kind"] == "programa")      # desinstaladores (interativos) sempre por ultimo
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
        job = JOBS[jid] = {"id": jid, "log": [], "results": {}, "done": False, "current": None, "total": len(plan), "freed": 0,
                           "items_done": 0, "w_done": 0, "w_total": 1, "cur": None, "cur_done": 0, "prompt": None,
                           "aborting": False, "aborted": False, "t0": time.time(), "drive": str(b.get("drive") or ""),
                           "plan_ids": [it["id"] for _, it, _ in plan]}
        ctx = CTXS[jid] = JobCtx(job)
        log("=== Nova operação %s: %s | apagar=%d mover=%d lixeira=%s esvaziar_lixeira=%s ===" % (jid, action, len(dels), len(moves), recycle, empty_bin))

        def runner():
            try:
                run_job(job, ctx, plan, empty_bin)
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
