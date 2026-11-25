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
4. Duas colunas de seleção: **Apagar** (programas são desinstalados pelo próprio desinstalador; jogos Steam pelo Steam) e **Mover**.
5. Para mover, o botão **“Transferir pasta para outro local”** abre o seletor de pastas do Explorer; o caminho escolhido aparece ao lado do botão.
6. Quatro botões no rodapé (valem **só para a aba aberta**): **Apagar tudo** · **Apagar selecionados** · **Mover arquivos especificados** · **Apagar selecionados e mover**.

### Segurança (o que ele garante e o que não garante)

- **Scan somente leitura.** Apagar/mover só ocorre depois de clique **+ dupla confirmação**; “Apagar tudo” ainda exige digitar `APAGAR TUDO`.
- **“Apagar tudo” pula itens de risco “Cuidado”** (documentos pessoais, Java, certificados digitais, ferramentas de desenvolvimento).
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

### Como usar como *skill* do Claude Code

Baixe `limpeza-de-disco-skill.zip` na página de [Releases](https://github.com/wagnerroadv-89/disk-declutter/releases), descompacte em `%USERPROFILE%\.claude\skills\` e rode **`/limpeza-de-disco`**. A skill extrai o app para `%USERPROFILE%\LimpezaDisco`, sobe o servidor e abre o painel. (Ou, do repositório: `python tools/build_zip.py --install`.)

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
3. Two checkbox columns — **Delete** (programs are uninstalled through their own uninstaller) and **Move** — plus a **“Transferir pasta para outro local”** button that opens the Explorer folder picker and shows the chosen path next to the button.
4. Four footer buttons (apply to the **current tab only**): **Delete everything** · **Delete selected** · **Move specified** · **Delete selected and move**.

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

**As a Claude Code skill:** download `limpeza-de-disco-skill.zip` from [Releases](https://github.com/wagnerroadv-89/disk-declutter/releases), unzip into `%USERPROFILE%\.claude\skills\`, run `/limpeza-de-disco`.

The UI is in Brazilian Portuguese; translations are welcome via PR. Tunable thresholds and regexes are at the top of [`app/scanner.py`](app/scanner.py).

### License

[MIT](LICENSE)
