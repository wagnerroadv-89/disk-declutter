"""Scanner de candidatos a limpeza de UM disco. SOMENTE LEITURA: mede tamanhos e datas, nunca altera nada.

Fontes de candidatos:
  1. Pastas e arquivos grandes que nao sao modificados ha muito tempo (varredura do disco todo)
  2. Programas instalados (registro do Windows) sem uso recente (registro UserAssist)
  3. Jogos Steam sem jogar ha muito tempo (campo LastPlayed dos manifestos do Steam)
  4. Caches/temporarios conhecidos (apenas no disco do sistema)
"""
import os, re, stat, time, hashlib, datetime, struct, winreg, sys
from concurrent.futures import ThreadPoolExecutor

sys.setrecursionlimit(8000)
DAY = 86400
REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT
OFFLINE = 0x1000 | 0x400000 | 0x40000      # arquivos so na nuvem (nao ocupam disco)

DIR_MIN = 1 * 2**30            # pasta parada so entra se tiver >= 1 GB
STALE_DAYS = 90                # ... e nenhuma modificacao ha >= 90 dias
FILE_MIN = 200 * 2**20         # arquivos a observar (>= 200 MB)
BIG_FILE_MIN = 1 * 2**30       # arquivo grande parado (>= 1 GB, 180 dias)
PROG_MIN = 50 * 2**20          # programa so entra se ocupar >= 50 MB
PROG_STALE_DAYS = 180          # ... e nao ter uso registrado ha >= 180 dias
STEAM_MIN = 300 * 2**20
CACHE_MIN = 30 * 2**20
MAXDEPTH = 5

C_BIG = "Pastas e arquivos grandes parados"
C_INST = "Instaladores e arquivos compactados antigos"
C_CACHE = "Cache e temporários (recriados automaticamente)"
C_PROG = "Programas e jogos instalados"
CAT_ORDER = {C_BIG: 0, C_INST: 1, C_CACHE: 2, C_PROG: 3}

SKIP_TOP = {"windows", "system volume information", "$recycle.bin", "recovery", "perflogs", "config.msi",
            "$winreagent", "documents and settings", "msocache", "$windows.~bt", "$sysreset", "windows.old",
            "program files", "program files (x86)", "programdata", "arquivos de programas", "$getcurrent"}
INSTALLER_EXT = {".exe", ".msi", ".msix", ".iso", ".img", ".zip", ".rar", ".7z", ".cab", ".dmp", ".bak", ".old", ".tmp"}
PERSONAL = ("\\documents\\", "\\documentos\\", "\\desktop\\", "\\área de trabalho\\", "\\pictures\\", "\\imagens\\",
            "\\videos\\", "\\vídeos\\", "\\music\\", "\\músicas\\", "\\onedrive")
SKIP_PROG = re.compile(
    r"(visual c\+\+|\.net|windows sdk|redistributable|runtime|directx|hotfix|update for|"
    r"nvidia (driver|graphics|hd|physx|frameview|app|software|geforce)|intel\(r\)|intel (graphics|driver)|amd |realtek|"
    r"microsoft edge|webview|windows (driver|software|sdk)|pacote de driver|driver package|microsoft update|"
    r"python launcher|gameinput|xbox|microsoft office|microsoft 365|onedrive|microsoft store|"
    r"vulkan|openal|visual studio|build tools|\bvc_|bonjour|apple mobile device|asus (framework|motherboard)|armoury|"
    r"aura (lighting|service)|rog live|fresco logic|vigem|corsair device)", re.I)
DEV_PROG = re.compile(r"(^git\b|git for windows|github|node\.js|nodejs|docker|python|wsl|windows subsystem|visual studio code|vs code|"
                      r"\bjdk\b|openjdk|\bnpm\b|powershell|cursor|windsurf|android studio|flutter|\bgo programming|rustup)", re.I)


def is_personal(path):
    low = path.lower() + "\\"
    if "\\users\\public\\" in low:
        return False
    return any(x in low for x in PERSONAL)


# Programas que costumam ser exigidos por certificado digital, banco ou sites do governo/tribunais.
# Editar esta lista (ou apagar entradas) muda quais programas aparecem como "Cuidado" e ficam fora do "Apagar tudo".
RISKY_PROG = re.compile(r"(java|certi|signer|safenet|safesign|warsaw|topaz|pje|token|assinador|e-cpf|smart ?card|pkcs|digital certificate)", re.I)


def now():
    return time.time()


def iso(ts):
    if not ts or ts < 86400 * 365:
        return None
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%.0f %s" if u in ("B", "KB") else "%.1f %s") % (n, u)
        n /= 1024.0


def months(ts):
    return max(1, int((now() - ts) / (DAY * 30.4)))


def dir_info(path):
    """(tamanho, mtime mais recente, qtde de arquivos) sem seguir junctions."""
    size = 0; newest = 0; n = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        a = getattr(st, "st_file_attributes", 0)
                        if a & REPARSE:
                            continue
                        if stat.S_ISDIR(st.st_mode):
                            stack.append(e.path)
                        elif not (a & OFFLINE):
                            size += st.st_size; n += 1
                            if st.st_mtime > newest:
                                newest = st.st_mtime
                    except OSError:
                        pass
        except OSError:
            pass
    return size, newest, n


def measure(path):
    try:
        if os.path.isdir(path):
            s, m, _ = dir_info(path)
            return s, m
        st = os.stat(path)
        return st.st_size, st.st_mtime
    except OSError:
        return None


class Walker:
    def __init__(self, st):
        self.st = st
        self.dirs = []        # (path, depth, size, newest)
        self.files = []       # (path, size, mtime)
        self.steam = []

    def walk(self, path, depth):
        size = 0; newest = 0
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            return 0, 0
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                a = getattr(st, "st_file_attributes", 0)
                if a & REPARSE:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if e.name.lower() == "steamapps":
                        self.steam.append(e.path)
                    s, n = self.walk(e.path, depth + 1)
                    size += s
                    if n > newest:
                        newest = n
                elif not (a & OFFLINE):
                    size += st.st_size
                    if st.st_mtime > newest:
                        newest = st.st_mtime
                    self.st["files"] += 1
                    if st.st_size >= FILE_MIN:
                        self.files.append((e.path, st.st_size, st.st_mtime))
            except OSError:
                pass
        if depth <= MAXDEPTH:
            self.dirs.append((path, depth, size, newest))
        return size, newest


def protected_dirs(drive):
    """Pastas que nunca viram candidato inteiras (so o conteudo delas pode)."""
    d = drive.upper() + "\\"
    p = {d.rstrip("\\"), d + "Windows", d + "Program Files", d + "Program Files (x86)", d + "ProgramData", d + "Users"}
    users = d + "Users"
    try:
        for n in os.listdir(users):
            u = os.path.join(users, n)
            p.add(u)
            for sub in ("AppData", "AppData\\Local", "AppData\\Roaming", "AppData\\LocalLow", "Documents", "Desktop", "Downloads",
                        "Pictures", "Videos", "Music", "OneDrive", "Documentos", "Imagens", "Vídeos", "Músicas", "Apple"):
                p.add(os.path.join(u, sub))
    except OSError:
        pass
    return {os.path.normcase(os.path.normpath(x)) for x in p}


def survey(drive, st):
    """Varre o disco todo (em paralelo pelas pastas de topo)."""
    root = drive + "\\"
    tops = []; extra_files = []
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    s = e.stat(follow_symlinks=False)
                    a = getattr(s, "st_file_attributes", 0)
                    if a & REPARSE or e.name.lower() in SKIP_TOP or e.name.startswith("$"):
                        continue
                    if stat.S_ISDIR(s.st_mode):
                        tops.append(e.path)
                    elif s.st_size >= FILE_MIN and not e.name.lower().endswith(".sys"):
                        extra_files.append((e.path, s.st_size, s.st_mtime))
                except OSError:
                    pass
    except OSError:
        pass
    walkers = []

    def job(p):
        w = Walker(st); w.walk(p, 1); walkers.append(w)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(job, tops))
    dirs = {}; files = list(extra_files); steam = []
    for w in walkers:
        for p, dep, s, n in w.dirs:
            dirs[p] = (dep, s, n)
        files += w.files; steam += w.steam
    return dirs, files, steam


def pick_stale_dirs(dirs, drive, roots=frozenset()):
    """Escolhe as pastas paradas e grandes (mais externas; divide se houver 2+ paradas dentro).
    `roots` = pastas de programas instalados: nunca viram candidato (nem inteiras nem por conterem uma)."""
    prot = protected_dirs(drive)
    kids = {}
    for p in dirs:
        kids.setdefault(os.path.dirname(p), []).append(p)
    t = now()

    def stale(p):
        dep, s, n = dirs[p]
        return s >= DIR_MIN and n > 0 and (t - n) >= STALE_DAYS * DAY and os.path.normcase(os.path.normpath(p)) not in prot

    def pick(p):
        dep, s, n = dirs[p]
        if s < DIR_MIN:
            return []
        np_ = os.path.normcase(os.path.normpath(p))
        if any(np_ == r or np_.startswith(r + "\\") for r in roots):
            return []                                  # e uma pasta de programa (ou esta dentro de uma)
        if stale(p) and not any(r.startswith(np_ + "\\") for r in roots):   # nem contem pasta de programa
            sk = [c for c in kids.get(p, []) if stale(c)]
            if len(sk) >= 2:
                out = []
                for c in sk:
                    out += pick(c)
                return out
            return [p]
        out = []
        for c in kids.get(p, []):
            out += pick(c)
        return out
    out = []
    for p, (dep, s, n) in dirs.items():
        if dep == 1:
            out += pick(p)
    return out


# ---------------------------------------------------------------- UserAssist / programas
def _rot13(s):
    return s.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                                     "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"))


def load_userassist():
    env = os.environ
    sysd = env.get("SystemDrive", "C:")
    guid = {
        "{6D809377-6AF0-444B-8957-A3773F02200E}": env.get("ProgramW6432", sysd + "\\Program Files"),
        "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": env.get("ProgramFiles(x86)", sysd + "\\Program Files (x86)"),
        "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": env.get("SystemRoot", sysd + "\\Windows"),
        "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": env.get("SystemRoot", sysd + "\\Windows") + "\\System32",
        "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}": env.get("LOCALAPPDATA", ""),
        "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}": env.get("APPDATA", ""),
        "{5E6C858F-0E22-4760-9AFE-EA3317B67173}": env.get("USERPROFILE", ""),
        "{62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}": env.get("ProgramData", sysd + "\\ProgramData"),
        "{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}": "LNK:", "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}": "LNK:",
        "{9E3995AB-1F9C-4F13-B827-48B24B6C7174}": "LNK:",
    }
    out = []
    try:
        base = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist")
    except OSError:
        return out
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(base, i); i += 1
        except OSError:
            break
        try:
            k = winreg.OpenKey(base, sub + "\\Count")
        except OSError:
            continue
        j = 0
        while True:
            try:
                name, data, typ = winreg.EnumValue(k, j); j += 1
            except OSError:
                break
            if typ != winreg.REG_BINARY or len(data) < 68:
                continue
            ft = struct.unpack_from("<q", data, 60)[0]
            if ft <= 116444736000000000:
                continue
            ts = (ft - 116444736000000000) / 1e7
            if ts < 946684800 or ts > now() + DAY:
                continue
            p = _rot13(name)
            m = re.match(r"(\{[0-9A-Fa-f-]+\})\\?(.*)", p)
            if m and m.group(1).upper() in guid:
                b = guid[m.group(1).upper()]
                p = ("LNK:" + m.group(2)) if b == "LNK:" else (b + "\\" + m.group(2))
            out.append((p.lower(), ts))
    return out


def _reg_values(key):
    d = {}
    i = 0
    while True:
        try:
            n, v, t = winreg.EnumValue(key, i); i += 1
            d[n] = v
        except OSError:
            break
    return d


def installed_programs():
    out = {}
    views = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0)]
    sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive, flag in views:
        try:
            base = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        i = 0
        while True:
            try:
                kn = winreg.EnumKey(base, i); i += 1
            except OSError:
                break
            try:
                k = winreg.OpenKey(base, kn, 0, winreg.KEY_READ | flag)
                v = _reg_values(k)
            except OSError:
                continue
            nm = str(v.get("DisplayName") or "").strip()
            if not nm or v.get("SystemComponent") == 1 or v.get("ParentKeyName") or v.get("ParentDisplayName"):
                continue
            if not (v.get("UninstallString") or v.get("QuietUninstallString")):
                continue
            loc = str(v.get("InstallLocation") or "").strip().strip('"').rstrip("\\")
            if not loc:
                ic = str(v.get("DisplayIcon") or "").split(",")[0].strip().strip('"')
                if ic and os.path.isfile(ic):
                    loc = os.path.dirname(ic)
            if loc and re.match(r"^[A-Za-z]:\\(windows|programdata\\package cache)", loc, re.I):
                loc = ""
            if loc and not os.path.isdir(loc):
                loc = ""
            out[(nm, kn)] = dict(name=nm, key=kn, version=v.get("DisplayVersion"), publisher=v.get("Publisher"), location=loc,
                                 machine=(hive == winreg.HKEY_LOCAL_MACHINE),   # instalado para todos os usuarios: o desinstalador costuma exigir admin
                                 est=int(v.get("EstimatedSize") or 0) * 1024, install=str(v.get("InstallDate") or ""),
                                 uninstall=str(v.get("UninstallString") or ""), quiet=str(v.get("QuietUninstallString") or ""))
    return list(out.values())


def _tokens(name):
    stop = {"microsoft", "windows", "update", "version", "x64", "x86", "driver", "drivers", "installer", "setup", "service",
            "services", "tools", "program", "suite", "google", "software", "application", "stable"}
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) >= 4 and t not in stop]


def steam_install_dirs(steam_dirs):
    """Pastas de jogos Steam instalados (steamapps\\common\\<installdir>), de todos os manifestos."""
    out = []
    for sa in sorted(set(steam_dirs)):
        try:
            names = os.listdir(sa)
        except OSError:
            continue
        for fn in names:
            if fn.startswith("appmanifest_") and fn.endswith(".acf"):
                try:
                    txt = open(os.path.join(sa, fn), encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                m = re.search(r'"installdir"\s+"([^"]*)"', txt)
                if m and m.group(1):
                    out.append(os.path.join(sa, "common", m.group(1)))
    return out


def program_roots(drive, progs, steam_dirs, protected):
    """Pastas que pertencem a programas instalados (registro do Windows + jogos Steam) neste disco.
    Programa so se tira com o desinstalador (nao se apaga nem se move a pasta), entao nenhuma pasta/arquivo
    dentro de uma raiz, nem uma pasta que CONTENHA uma raiz, pode virar item de apagar/mover."""
    roots = set()
    for p in progs:
        loc = p["location"]
        if loc and len(loc) > 3 and loc[:2].upper() == drive.upper():
            n = os.path.normcase(os.path.normpath(loc))
            if n not in protected and len(n.split("\\")) > 2:     # ignora "C:\Program Files" e afins
                roots.add(n)
    for d in steam_install_dirs(steam_dirs):
        if d[:2].upper() == drive.upper():
            roots.add(os.path.normcase(os.path.normpath(d)))
    return roots


def program_candidates(drive, system_drive, ua, progs=None):
    items = []
    t = now()
    progs = progs if progs is not None else installed_programs()
    seen_loc = {}
    for p in progs:
        loc = p["location"]
        pdrive = (loc[:2].upper() if loc else system_drive.upper())
        if pdrive != drive.upper():
            continue
        if "\\steamapps\\" in (loc + "\\").lower() or "\\microsoft office" in loc.lower() or SKIP_PROG.search(p["name"]):
            continue
        last = None
        toks = _tokens(p["name"])
        for path, ts in ua:
            hit = False
            if loc and len(loc) > 10 and path.startswith(loc.lower() + "\\"):
                hit = True
            elif toks and (path.startswith("lnk:") or ".exe" in path) and toks[0] in path and not path.startswith("c:\\windows"):
                hit = True
            if hit and (last is None or ts > last):
                last = ts
        if last and (t - last) < PROG_STALE_DAYS * DAY:
            continue
        shared = False
        size = 0
        if loc and loc.lower() not in ("c:\\program files", "c:\\program files (x86)") and len(loc) > 3:
            if loc.lower() in seen_loc:
                shared = True
            else:
                size = dir_info(loc)[0]
                seen_loc[loc.lower()] = p["name"]
        if not size and not shared:
            size = p["est"]
        if not shared and size < PROG_MIN:
            continue
        risky = bool(RISKY_PROG.search(p["name"] + " " + str(p["publisher"] or "")))
        if DEV_PROG.search(p["name"]):
            risk = "alto"; note = "Ferramenta de desenvolvimento: é usada pelo terminal/editor e quase nunca aparece no registro de uso do Windows."
        elif risky:
            risk = "alto"; note = "Pode ser exigido por certificado digital, banco ou sistemas de tribunais. Só remova se tiver certeza."
        elif last:
            risk = "baixo"; note = "Sem uso registrado há %d meses." % months(last)
        else:
            risk = "medio"; note = "O Windows não tem registro de uso deste programa (pode ter sido aberto de outro jeito). Confirme que não usa mais."
        if shared:
            note += " Divide a pasta de instalação com “%s”: o tamanho já está contado lá." % seen_loc[loc.lower()]
        inst = p["install"]
        ll = "Último uso (registro do Windows)" if last else "Sem registro de uso no Windows"
        if not last and len(inst) == 8 and inst.isdigit():
            ll += " · instalado em %s/%s/%s" % (inst[6:8], inst[4:6], inst[:4])
        items.append(dict(cat=C_PROG, name=p["name"], path=loc, kind="programa", risk=risk, note=note, movable=False, admin=False,
                          size=0 if shared else size, last=iso(last) if last else None, lastLabel=ll, action="uninstall",
                          uninstall=p["uninstall"], key=p["key"], publisher=p["publisher"], drive=drive.upper(),
                          uninstall_admin=bool(p.get("machine"))))
    return items


def steam_candidates(drive, steam_dirs, ua=None):
    items = []
    t = now()
    for sa in sorted(set(steam_dirs)):
        try:
            names = os.listdir(sa)
        except OSError:
            continue
        for fn in names:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            try:
                txt = open(os.path.join(sa, fn), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue

            def g(k):
                m = re.search(r'"%s"\s+"([^"]*)"' % k, txt)
                return m.group(1) if m else ""
            appid, name, idir = g("appid"), g("name"), g("installdir")
            last = int(g("LastPlayed") or 0); size = int(g("SizeOnDisk") or 0)
            path = os.path.join(sa, "common", idir)
            if not appid or not os.path.isdir(path) or size < STEAM_MIN:
                continue
            if last and (t - last) < PROG_STALE_DAYS * DAY:
                continue
            if last:
                note = "Jogo Steam sem jogar há %d meses. Dá para baixar de novo pelo Steam; os saves em nuvem não são afetados." % months(last)
                ll = "Última vez jogado (Steam)"
            else:
                note = "Jogo Steam que o Steam registra como nunca jogado. Dá para baixar de novo quando quiser."
                ll = "Nunca jogado (segundo o Steam)"
            items.append(dict(cat=C_PROG, name=name + " (Steam)", path=path, kind="programa", risk="medio", note=note, movable=False,
                              admin=False, size=size, last=iso(last) if last else None, lastLabel=ll, action="uninstall",
                              uninstall='start "" steam://uninstall/%s' % appid, key=None, steam=True, drive=drive.upper()))
    return items


def known_junk(drive):
    """Caches e temporarios conhecidos (so existem no disco do sistema)."""
    env = os.environ
    lad = env.get("LOCALAPPDATA", ""); up = env.get("USERPROFILE", ""); win = env.get("SystemRoot", "C:\\Windows")
    sysd = env.get("SystemDrive", "C:")
    L = []   # (nome, caminho, tipo, risco, nota, admin)
    def add(name, path, kind="conteudo", risk="baixo", note="", admin=False, trusted=False):
        L.append((name, path, kind, risk, note, admin, trusted))
    add("Pasta Temp do usuário (conteúdo)", env.get("TEMP", lad + "\\Temp"), note="Temporários de programas (instaladores velhos, logs...). Arquivos em uso são ignorados.")
    add("Cache do npm", lad + "\\npm-cache", note="Pacotes npm baixados; voltam a ser baixados quando necessário.")
    add("Cache do pip (Python)", lad + "\\pip\\Cache", note="Pacotes Python em cache.")
    add("Cache do Hugging Face (modelos de IA)", up + "\\.cache\\huggingface", note="Modelos de IA baixados; são baixados de novo ao usar.")
    add("Cache do PyTorch", up + "\\.cache\\torch", note="Modelos baixados; voltam a ser baixados ao usar.")
    add("Modelos de remoção de fundo (.rembg)", up + "\\.rembg", kind="pasta", note="Modelos do rembg; baixados de novo ao usar.")
    add("Cache do Gradle", up + "\\.gradle\\caches", note="Dependências em cache; o Gradle baixa de novo.")
    add("Cache de shaders NVIDIA (DXCache)", lad + "\\NVIDIA\\DXCache", note="Shaders compilados; os jogos recriam (pode travar um pouco na 1ª vez).")
    add("Cache de shaders NVIDIA (GLCache)", lad + "\\NVIDIA\\GLCache", note="Shaders compilados; recriados automaticamente.")
    add("Cache de shaders DirectX (D3DSCache)", lad + "\\D3DSCache", note="Shaders compilados; recriados automaticamente.")
    add("Relatórios de falha (CrashDumps)", lad + "\\CrashDumps", note="Dumps de travamentos de programas.")
    add("Cache de páginas do Steam (htmlcache)", lad + "\\Steam\\htmlcache", note="Cache do navegador interno do Steam. Feche o Steam antes.")
    for br, base in (("Chrome", lad + "\\Google\\Chrome\\User Data"), ("Edge", lad + "\\Microsoft\\Edge\\User Data"),
                     ("Brave", lad + "\\BraveSoftware\\Brave-Browser\\User Data")):
        add("Modelo de IA local do %s (OptGuideOnDeviceModel)" % br, base + "\\OptGuideOnDeviceModel", kind="pasta",
            note="Modelo de IA que o navegador baixa sozinho. Feche o %s antes; ele pode baixar de novo." % br)
        for sub in ("Cache", "Code Cache", "GPUCache"):
            add("Cache do %s (%s)" % (br, sub), base + "\\Default\\" + sub, note="Cache de navegação. Feche o %s antes." % br)
    add("Cache do Windows Update (SoftwareDistribution\\Download)", win + "\\SoftwareDistribution\\Download",
        note="Atualizações do Windows já baixadas. Requer administrador.", admin=True, trusted=True)
    add("Temporários do Windows (Windows\\Temp)", win + "\\Temp", note="Temporários do sistema. Requer administrador.", admin=True, trusted=True)
    add("Arquivo de hibernação (hiberfil.sys)", sysd + "\\hiberfil.sys", kind="sistema", risk="medio",
        note="Libera o espaço desativando a hibernação (powercfg /h off). Também desliga a “inicialização rápida”. Requer administrador.",
        admin=True, trusted=True)
    items = []
    for name, path, kind, risk, note, admin, trusted in L:
        if not os.path.exists(path):
            continue
        m = measure(path)
        if not m or m[0] < CACHE_MIN:
            continue
        it = dict(cat=C_CACHE, name=name, path=path, kind=kind, risk=risk, note=note, movable=False, admin=admin,
                  size=m[0], last=None, lastLabel="Arquivo do sistema" if kind == "sistema" else "Cache", action=None, drive=drive.upper(), trusted=trusted)
        if kind == "sistema":
            it["action"] = "powercfg /h off"
        items.append(it)
    # evita listar duas vezes a mesma pasta (TEMP e LOCALAPPDATA\Temp)
    uniq = {}
    for it in items:
        uniq.setdefault(os.path.normcase(it["path"]), it)
    return list(uniq.values())


def scan_drive(drive, st):
    """Retorna a lista de candidatos do disco (ex.: 'C:'). `st` e atualizado com o andamento."""
    drive = drive.upper().rstrip("\\")
    sysd = os.environ.get("SystemDrive", "C:").upper()
    st.update(phase="Varrendo pastas e arquivos…", files=0, found=0)
    dirs, files, steam = survey(drive, st)
    st["phase"] = "Lendo programas e jogos instalados…"
    ua = load_userassist()
    out = []
    if drive == sysd:
        out += known_junk(drive)
    progs = installed_programs()
    roots = program_roots(drive, progs, steam, protected_dirs(drive))
    out += program_candidates(drive, sysd, ua, progs)
    out += steam_candidates(drive, steam)
    st["found"] = len(out)
    st["phase"] = "Analisando o que está parado…"

    kept = [os.path.normcase(os.path.normpath(i["path"])) for i in out if i["path"]]
    t = now()
    tail = []
    for p in pick_stale_dirs(dirs, drive, roots):
        dep, size, newest = dirs[p]
        np_ = os.path.normcase(os.path.normpath(p))
        if "\\steamapps\\" in np_ + "\\":      # arquivos internos de jogos Steam: o jogo e tratado como um todo
            continue
        # nao listar pasta que contem algo ja listado (programa/cache) ou que esta dentro de um
        if any(k == np_ or k.startswith(np_ + "\\") or np_.startswith(k + "\\") for k in kept):
            continue
        low = p.lower() + "\\"
        base = os.path.basename(p.rstrip("\\")) or p
        if is_personal(p):
            risk = "alto"; note = "Parece conteúdo pessoal (documentos/mídia). Prefira MOVER para outro disco em vez de apagar."
        elif "\\mobilesync\\backup" in low:
            risk = "medio"; note = "Backup local de iPhone/iPad (iTunes/Apple Devices). Confira se o aparelho/iCloud já tem tudo; o ideal é MOVER."
        else:
            risk = "medio"; note = "Pasta de %s sem nenhuma modificação há %d meses. Confira o conteúdo antes de decidir." % (human(size), months(newest))
        tail.append(dict(cat=C_BIG, name="Pasta parada: " + base, path=p, kind="pasta", risk=risk, note=note, movable=True, admin=False,
                         size=size, last=iso(newest), lastLabel="Última modificação", action=None, drive=drive))
        kept.append(np_)
    for p, size, mt in sorted(files, key=lambda x: -x[1]):
        np_ = os.path.normcase(os.path.normpath(p))
        age = t - mt
        ext = os.path.splitext(p)[1].lower()
        if any(np_.startswith(k + "\\") for k in kept) or any(np_.startswith(r + "\\") for r in roots):
            continue          # arquivo dentro de algo ja listado ou dentro da pasta de um programa instalado
        if ext in INSTALLER_EXT and age >= 60 * DAY:
            cat, risk = C_INST, "baixo"
            note = "Instalador/arquivo compactado de %s, parado há %d meses. Se o programa já está instalado, pode apagar." % (human(size), months(mt))
        elif size >= BIG_FILE_MIN and age >= 180 * DAY:
            cat, risk = C_BIG, "medio"
            note = "Arquivo de %s sem modificação há %d meses. Confira antes de decidir." % (human(size), months(mt))
        else:
            continue
        if is_personal(p):
            risk = "alto"; note += " Está numa pasta pessoal."
        tail.append(dict(cat=cat, name=os.path.basename(p), path=p, kind="arquivo", risk=risk, note=note, movable=True, admin=False,
                         size=size, last=iso(mt), lastLabel="Última modificação", action=None, drive=drive))
        kept.append(np_)
    out += tail
    for it in out:
        it["id"] = hashlib.sha1(("%s|%s|%s|%s" % (it["kind"], it["drive"], it["path"], it["name"])).encode("utf-8", "ignore")).hexdigest()[:12]
    out.sort(key=lambda r: (CAT_ORDER.get(r["cat"], 9), -r["size"]))
    st["found"] = len(out)
    return out
