"""Gera os pacotes em dist/ e, com --install, instala a skill em ~/.claude/skills/limpeza-de-disco.

  python tools/build_zip.py              # gera dist/limpeza-de-disco-templates.zip e dist/limpeza-de-disco-skill.zip
  python tools/build_zip.py --install    # idem + instala a skill no Claude Code

O zip "templates" e o app inteiro; o zip "skill" e a pasta limpeza-de-disco/ (SKILL.md + templates.zip).
No fim, confere que o conteudo dos zips e identico aos arquivos-fonte.
"""
import os, sys, zipfile, shutil, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
DIST = os.path.join(ROOT, "dist")
APP_FILES = ["server.py", "scanner.py", "index.html", "iniciar.bat", "iniciar-admin.bat", "LEIA-ME.txt", "VERSION"]
TEMPLATES = os.path.join(DIST, "limpeza-de-disco-templates.zip")
PACKAGE = os.path.join(DIST, "limpeza-de-disco-skill.zip")
SKILL_MD = os.path.join(ROOT, "skill", "SKILL.md")
SKILL_DIR = os.path.join(os.path.expanduser("~"), ".claude", "skills", "limpeza-de-disco")


def sha(b):
    return hashlib.sha256(b).hexdigest()[:12]


os.makedirs(DIST, exist_ok=True)
with zipfile.ZipFile(TEMPLATES, "w", zipfile.ZIP_DEFLATED) as z:
    for f in APP_FILES:
        z.write(os.path.join(APP, f), f)
with zipfile.ZipFile(PACKAGE, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(SKILL_MD, "limpeza-de-disco/SKILL.md")
    z.write(TEMPLATES, "limpeza-de-disco/templates.zip")
print("gerado:", TEMPLATES, os.path.getsize(TEMPLATES), "bytes")
print("gerado:", PACKAGE, os.path.getsize(PACKAGE), "bytes")

if "--install" in sys.argv:
    os.makedirs(SKILL_DIR, exist_ok=True)
    shutil.copy2(SKILL_MD, os.path.join(SKILL_DIR, "SKILL.md"))
    shutil.copy2(TEMPLATES, os.path.join(SKILL_DIR, "templates.zip"))
    print("skill instalada em:", SKILL_DIR)

ok = True
with zipfile.ZipFile(TEMPLATES) as z:
    for f in APP_FILES:
        same = sha(z.read(f)) == sha(open(os.path.join(APP, f), "rb").read())
        ok &= same
        print("  %-18s %s" % (f, "ok" if same else "DIFERENTE"))
with zipfile.ZipFile(PACKAGE) as z:
    ok &= sha(z.read("limpeza-de-disco/templates.zip")) == sha(open(TEMPLATES, "rb").read())
    ok &= sha(z.read("limpeza-de-disco/SKILL.md")) == sha(open(SKILL_MD, "rb").read())
print("VERIFICACAO:", "tudo identico aos arquivos-fonte" if ok else "FALHOU")
sys.exit(0 if ok else 1)
