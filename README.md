# disk-declutter

**🇧🇷 [Português](#-português) · 🇺🇸 [English](#-english)**

Painel local para **descobrir o que está ocupando espaço à toa nos seus discos do Windows** — pastas e arquivos grandes esquecidos, instaladores antigos, programas e jogos sem uso, caches — e **apagar, desinstalar ou mover para outro disco** só o que você escolher.
*A local Windows panel that finds what is wasting space on your drives and lets you delete, uninstall or move only what you pick.*

- 🔒 Roda 100% na sua máquina (`127.0.0.1`), sem nuvem, sem telemetria, sem dependências (só Python).
- 👀 O scan **só lê**; nada muda até você marcar itens, clicar e **confirmar duas vezes**.
- 🗂️ Uma **aba por disco**, lista em tabela: nome · último uso · tamanho · apagar / mover.

---

## 🇧🇷 Português

### Para que serve

Disco cheio e você não sabe o que apagar? O **disk-declutter** varre as unidades que você escolher e monta uma lista do que *provavelmente* pode sair, explicando o motivo de cada item, quando foi usado pela última vez e quanto espaço libera. Você marca o que quer apagar ou mover e ele executa — com confirmações e verificação.

### O que ele faz

1. **Lista os discos** do Windows (fixos e removíveis) com espaço livre. Você marca quais escanear e clica em **Escanear agora**.
2. **Escaneia só lendo**, em paralelo, e abre **uma aba para cada disco** com os candidatos:

   | Categoria | Critério |
   |---|---|
   | Pastas e arquivos grandes parados | Pastas ≥ 1 GB sem nenhuma modificação há 90+ dias; arquivos ≥ 1 GB parados há 180+ dias |
   | Instaladores e arquivos compactados antigos | `.exe .msi .iso .zip .rar .7z .cab .bak .dmp …` ≥ 200 MB parados há 60+ dias |
   | Programas e jogos instalados | Programas sem uso registrado pelo Windows (UserAssist) há 180+ dias ou sem registro; jogos Steam sem jogar (campo `LastPlayed` do Steam) |
   | Cache e temporários | Temp, npm/pip/Gradle, shaders, crash dumps, caches de navegadores, Windows Update, hibernação… (apenas no disco do sistema) |

3. Mostra cada item numa **tabela** com *nome · último uso · tamanho*, um **selo de risco** (*Baixo risco*, *Confirme antes*, *Cuidado*) e uma explicação.
4. Duas colunas de seleção: **Apagar / Desinstalar** e **Mover**. **A pasta de um programa instalado nunca é oferecida para apagar nem para mover** (mover exigiria desinstalar e reinstalar): programas e jogos aparecem **só para desinstalar** — pelo desinstalador do próprio programa (jogos Steam, pelo Steam). Pastas e arquivos que pertencem a um programa instalado (ou que *contêm* um) são ignorados no scan.
5. **O caminho de cada item é um link clicável** (📂): um clique abre o Explorer do Windows já com o arquivo/pasta selecionado (em caches, abre dentro da pasta), para você conferir antes de decidir — sem copiar e colar caminhos.
6. Para mover, o botão **“Transferir pasta para outro local”** abre o seletor de pastas do Explorer; o caminho escolhido aparece ao lado do botão.
7. Oito botões no rodapé, em **três grupos** separados por uma barra vertical (valem **só para a aba aberta**):

   | Grupo | Botões |
   |---|---|
   | **Arquivos e pastas** | Apagar todos os arquivos · Apagar arquivos selecionados · Mover arquivos especificados · Apagar selecionados e mover |
   | **Programas (só desinstalar)** | Desinstalar todos os programas · Desinstalar programas selecionados |
   | **Arquivos + programas** | Apagar todos os arquivos e desinstalar programas · Apagar arquivos e programas selecionados |

   Os botões “todos” pedem para digitar `APAGAR TUDO`, `DESINSTALAR TUDO` ou `APAGAR E DESINSTALAR TUDO`, conforme o grupo. Quando há arquivos e programas na mesma ação, os arquivos são apagados primeiro e os programas desinstalados por último, um por vez.

### ⚠️ Desinstalar programas pode exigir administrador

- Os desinstaladores abrem a **janela do próprio programa** e você confirma nela. O painel confere no registro do Windows se o programa realmente saiu.
- Programas instalados **para todos os usuários** (a maioria, em `Program Files`) costumam exigir **administrador**; o painel os marca com *“desinstalar pode exigir administrador”* e mostra um aviso na aba.
- Para evitar erro, **inicie o painel como administrador**: `app\iniciar-admin.bat` (o Windows mostra a janela de permissão, o UAC, que só você pode aprovar) — ou, pela skill, escolha **“Como administrador”**.
- No modo normal você ainda pode tentar: o Windows pode pedir permissão na hora; se o desinstalador exigir elevação e não conseguir, o painel mostra *“Este desinstalador exige ADMINISTRADOR (código 740)”* e **não marca o programa como removido**.
- Programas instalados só para o seu usuário (e jogos Steam) não precisam de administrador.

### Segurança (o que ele garante e o que não garante)

- **Scan somente leitura.** Apagar/mover/desinstalar só ocorre depois de clique **+ dupla confirmação**; os botões “todos” ainda exigem digitar um texto (acima).
- **Os botões “todos” pulam itens de risco “Cuidado”** (documentos pessoais, Java, certificados digitais, ferramentas de desenvolvimento).
- **Lixeira por padrão** para arquivos e pastas comuns (caches são apagados direto). Opção de **esvaziar a Lixeira** ao concluir (esvazia a Lixeira inteira do Windows — o aviso aparece antes).
- **Mover = copiar → verificar (nº de arquivos e bytes) → só então apagar a origem**; opcionalmente deixa uma *junction* (atalho de pasta) no local antigo para que programas continuem achando os arquivos.
- O servidor **só age sobre itens que ele mesmo listou** no scan (nunca aceita caminhos arbitrários), recusa pastas de sistema/perfil e **não segue junctions** ao apagar.
- Escuta apenas em `127.0.0.1`, com **token** novo a cada execução e checagem do cabeçalho `Host`.
- Tudo que foi executado fica registrado em `limpeza.log`.
- **O resultado do scan fica só na memória.** Cada abertura nova do painel começa na tela de escolher discos e exige novo scan; se você fechar sem querer, o scan é mantido por **10 minutos** (configurável com `LIMPEZA_TTL_SEC`) e depois é descartado.

> ⚠️ **Use por sua conta e risco.** Os critérios são *heurísticas*: “sem uso registrado” não significa “inútil” (ex.: ferramentas de linha de comando não aparecem no registro de uso do Windows). Leia o motivo de cada item e confira antes de apagar. Veja a licença (MIT, sem garantia).

### Requisitos

- Windows 10 ou 11
- [Python 3.9+](https://www.python.org/downloads/) no PATH, com **tcl/tk** (já vem no instalador oficial) — usado só para a janela de escolher pasta
- Nenhum pacote pip

### Como usar (sem Claude)

```powershell
git clone https://github.com/wagnerroadv-89/disk-declutter.git
cd disk-declutter\app
python -I server.py        # ou dê dois cliques em iniciar.bat
```

O navegador abre em `http://127.0.0.1:8765/`. Marque os discos → **Escanear agora**.
Para itens que exigem administrador (`Windows\Temp`, cache do Windows Update, hibernação) use `iniciar-admin.bat`.

### Como usar como *skill* do Claude

Baixe `limpeza-de-disco-skill.zip` (ou `limpeza-de-disco.skill`, mesmo conteúdo) na página de [Releases](https://github.com/wagnerroadv-89/disk-declutter/releases) e **envie no Claude** (*Plugins → Enviar skill*: o pacote **não tem zip aninhado**, que o enviador recusa) ou descompacte em `%USERPROFILE%\.claude\skills\`; depois rode **`/limpeza-de-disco`**. A skill copia o app (pasta `app/` do pacote) para `%USERPROFILE%\LimpezaDisco`, **pergunta a cada execução se você quer iniciar em modo normal ou como administrador**, abre o `.bat` correspondente (`iniciar.bat` ou `iniciar-admin.bat`) e abre o painel. No modo administrador o Windows mostra a janela de permissão (UAC), que só você pode aprovar. Se já houver um painel rodando no modo escolhido, ele é reaproveitado; se o modo for diferente, o servidor é reiniciado (e o scan guardado é descartado). (Ou, do repositório: `python tools/build_zip.py --install`.)

### Personalizar

No topo de [`app/scanner.py`](app/scanner.py): `DIR_MIN`, `STALE_DAYS`, `FILE_MIN`, `BIG_FILE_MIN`, `PROG_MIN`, `PROG_STALE_DAYS`, a lista de caches conhecidos (`known_junk`) e as expressões `SKIP_PROG` / `RISKY_PROG` / `DEV_PROG` que decidem o que é ignorado ou marcado como “Cuidado”.

### Estrutura

```
app/        server.py (API + apagar/mover) · scanner.py (heurísticas) · index.html (painel) · iniciar*.bat
skill/      SKILL.md da skill do Claude Code
tools/      build_zip.py (gera dist/*.zip e instala a skill)
```

### Limitações conhecidas

- Somente Windows. Programas só são **desinstalados** (não movidos: mover instalação exige reinstalar).
- O “último uso” de programas vem do registro **UserAssist** (só pega o que foi aberto pelo Explorer/atalhos). Para jogos Steam usa o próprio Steam.
- Scan de discos de rede/nuvem não é feito (arquivos “somente online” do OneDrive/Google Drive são ignorados).
- Mover pastas de programas em uso pode falhar; o app avisa e mantém a origem.

### Contribuindo

Issues e PRs são bem-vindos — principalmente novos caches conhecidos, ajustes de heurística e relatos de falsos positivos.

### Licença

[MIT](LICENSE)

---

## 🇺🇸 English

### What it is

**disk-declutter** is a local, Windows-only web panel that scans the drives you pick and lists what is *probably* safe to remove: big forgotten folders and files, old installers, programs and Steam games you no longer use, and caches. For each item it shows **name · last used · size**, a **risk badge** and the reason it was listed. You tick what to **delete/uninstall** or **move to another drive** — nothing happens until you confirm twice.

### How it works

1. Lists your Windows drives; you tick which ones to scan and press **Escanear agora** (“Scan now”).
2. Scans **read-only**, in parallel, and opens **one tab per drive** with candidates:
   - folders ≥ 1 GB untouched for 90+ days, files ≥ 1 GB untouched for 180+ days;
   - installers/archives ≥ 200 MB untouched for 60+ days;
   - programs with no recorded use for 180+ days (Windows *UserAssist*), and Steam games not played (Steam `LastPlayed`);
   - well-known caches/temp files (system drive only).
3. Each item's **path is a clickable link**: one click opens Windows Explorer with the file/folder already selected, so you can check it before deciding.
4. Two checkbox columns — **Delete / Uninstall** and **Move** — plus a **“Transferir pasta para outro local”** button that opens the Explorer folder picker and shows the chosen path next to the button. **An installed program's folder is never offered for deletion or moving** (moving would need an uninstall + reinstall): programs and games are offered **for uninstall only**, through their own uninstaller (Steam games through Steam). Folders/files that belong to an installed program, or that contain one, are skipped by the scan.
5. Eight footer buttons in **three groups** separated by a vertical bar (apply to the **current tab only**): *Files and folders* (delete all files · delete selected files · move specified · delete selected and move), *Programs — uninstall only* (uninstall all programs · uninstall selected programs) and *Files + programs* (delete all files and uninstall programs · delete selected files and programs). The “all” buttons require typing `APAGAR TUDO`, `DESINSTALAR TUDO` or `APAGAR E DESINSTALAR TUDO`. Files go first, programs last, one at a time.

> ⚠️ **Uninstalling programs may require administrator rights.** Uninstallers open the program's own window. Programs installed *for all users* (most of `Program Files`) usually need admin: the panel tags them *“desinstalar pode exigir administrador”* and shows a banner. To avoid errors start the panel as administrator (`app\iniciar-admin.bat`, or choose “Como administrador” in the skill) — Windows shows a UAC prompt that only you can approve. In normal mode you can still try; if the uninstaller needs elevation the panel reports *“exige ADMINISTRADOR (código 740)”* and does **not** mark the program as removed.

### Safety

Read-only scan · two confirmations on every action (plus typing `APAGAR TUDO` for “delete everything”) · “delete everything” skips *Caution* items · Recycle Bin by default (optional “empty Recycle Bin when done”) · moves are copy → verify (file count and bytes) → delete source, optionally leaving a directory junction · the server acts only on items it listed itself, refuses system/profile folders and never follows junctions · binds to `127.0.0.1` only with a per-run token · scan results live **in memory only** and are discarded 10 minutes after the panel is closed (re-opening within 10 min restores them) · every action is logged to `limpeza.log`.

> ⚠️ **Use at your own risk.** The criteria are heuristics. “No recorded use” does not mean “useless”. Review each item before deleting. MIT license, no warranty.

### Requirements & usage

Windows 10/11 and Python 3.9+ with tcl/tk on PATH (no pip packages).

```powershell
git clone https://github.com/wagnerroadv-89/disk-declutter.git
cd disk-declutter\app
python -I server.py
```

Open `http://127.0.0.1:8765/`, tick the drives, press **Escanear agora**. Use `iniciar-admin.bat` for admin-only items.

**As a Claude skill:** download `limpeza-de-disco-skill.zip` (or `limpeza-de-disco.skill`, same content) from [Releases](https://github.com/wagnerroadv-89/disk-declutter/releases) and either upload it in Claude (*Plugins → Upload skill*; the package has **no nested zip**, which the uploader rejects) or unzip it into `%USERPROFILE%\.claude\skills\`; then run `/limpeza-de-disco`. Every run the skill asks whether to start **normally or as administrator** and launches the matching `.bat` (the Windows UAC prompt must be approved by you).

The UI is in Brazilian Portuguese; translations are welcome via PR. Tunable thresholds and regexes are at the top of [`app/scanner.py`](app/scanner.py).

### License

[MIT](LICENSE)
