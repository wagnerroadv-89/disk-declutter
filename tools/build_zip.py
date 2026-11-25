"""Gera os pacotes em dist/ e, com --install, instala a skill em ~/.claude/skills/limpeza-de-disco.

  python tools/build_zip.py              # gera os pacotes em dist/
  python tools/build_zip.py --install    # idem + instala a skill no Claude Code

Pacotes gerados:
  limpeza-de-disco-skill.zip  e  limpeza-de-disco.skill   (mesmo conteudo; para "Enviar skill" do Claude)
      limpeza-de-disco/SKILL.md
      limpeza-de-disco/app/...   <- o app inteiro, SEM nenhum .zip dentro (o uploader do Claude recusa zip aninhado)
  limpeza-de-disco-templates.zip   (so o app, para uso fora do Claude)

No fim, confere que o conteudo dos pacotes e identico aos arquivos-fonte e que nao ha zip aninhado.
"""
import os, sys, zipfile, shutil, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DIST = os.path.join(ROOT, "dist")
APP_FILES = ["server.py", "scanner.py", "index.html", "iniciar.bat", "iniciar-admin.bat", "LEIA-ME.txt", "VERSION"]
SKILL_MD = os.path.join(ROOT, "skill", "SKILL.md")
SKILL_ZIP = os.path.join(DIST, "limpeza-de-disco-skill.zip")
SKILL_FILE = os.path.join(DIST, "limpeza-de-disco.skill")
APP_ZIP = os.path.join(DIST, "limpeza-de-disco-templates.zip")
SKILL_DIR = os.path.join(os.path.expanduser("~"), ".claude", "skills", "limpeza-de-disco")


def sha(b):
    return hashlib.sha256(b).hexdigest()[:12]


def read(p):
    with open(p, "rb") as f:
        return f.read()


os.makedirs(DIST, exist_ok=True)
for out in (SKILL_ZIP, SKILL_FILE):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SKILL_MD, "limpeza-de-disco/SKILL.md")
        for f in APP_FILES:
            z.write(os.path.join(APP, f), "limpeza-de-disco/app/" + f)
with zipfile.ZipFile(APP_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
    for f in APP_FILES:
        z.write(os.path.join(APP, f), f)
for p in (SKILL_ZIP, SKILL_FILE, APP_ZIP):
    print("gerado:", p, os.path.getsize(p), "bytes")

if "--install" in sys.argv:
    os.makedirs(SKILL_DIR, exist_ok=True)
    shutil.copy2(SKILL_MD, os.path.join(SKILL_DIR, "SKILL.md"))
    dst = os.path.join(SKILL_DIR, "app")
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for f in APP_FILES:
        shutil.copy2(os.path.join(APP, f), os.path.join(dst, f))
    old = os.path.join(SKILL_DIR, "templates.zip")        # formato antigo (zip aninhado): nao e mais usado
    if os.path.exists(old):
        os.remove(old)
    print("skill instalada em:", SKILL_DIR)

ok = True
for pkg in (SKILL_ZIP, SKILL_FILE):
    with zipfile.ZipFile(pkg) as z:
        names = z.namelist()
        nested = [n for n in names if n.lower().endswith((".zip", ".skill", ".plugin"))]
        ok &= not nested
        ok &= sha(z.read("limpeza-de-disco/SKILL.md")) == sha(read(SKILL_MD))
        for f in APP_FILES:
            ok &= sha(z.read("limpeza-de-disco/app/" + f)) == sha(read(os.path.join(APP, f)))
        print("  %-30s %d arquivos | zip aninhado: %s" % (os.path.basename(pkg), len(names), "SIM (ruim)" if nested else "nao"))
with zipfile.ZipFile(APP_ZIP) as z:
    for f in APP_FILES:
        ok &= sha(z.read(f)) == sha(read(os.path.join(APP, f)))
print("VERIFICACAO:", "tudo identico aos arquivos-fonte, sem zip aninhado" if ok else "FALHOU")
sys.exit(0 if ok else 1)
