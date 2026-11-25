# AGENTS.md — instruções para agentes de IA (Claude Code, Codex, Cursor, Copilot, Gemini CLI…)

Este repositório é o **disk-declutter**: um painel local (Python, `127.0.0.1`) para escanear discos do Windows e **apagar, desinstalar ou mover** só o que o usuário escolher. Se um usuário pedir a você para "limpar o disco / liberar espaço / abrir o painel", siga isto.

## Regras que valem sempre

1. **Você não apaga, desinstala nem move nada.** Quem faz isso é o painel, depois de o usuário marcar itens, clicar e **confirmar duas vezes**. Nunca chame `/api/run` nem clique nos botões de ação em nome do usuário.
2. O scan é somente leitura; o usuário o dispara no painel (só dispare se ele pedir).
3. Não envie a lista (nomes de pastas/programas do usuário) a serviços externos.
4. Você **não pode aprovar** a janela de permissão do Windows (UAC): só o usuário.

## ⚠️ AVISO OBRIGATÓRIO NA PRIMEIRA EXECUÇÃO — desinstalar programas pode exigir administrador

**Na primeira vez que você rodar este app para o usuário** (no seu console/terminal/IDE ou em qualquer outra IA — e não só abrindo a pasta e dando dois cliques no `.bat`), diga a ele, antes de abrir o painel:

> Desinstalar programas pode exigir **administrador**. Programas instalados para todos os usuários (a maioria, em `Program Files`) costumam pedir isso; o painel marca esses com *"desinstalar pode exigir administrador"*. Para evitar erro, inicie como administrador (`app\iniciar-admin.bat`): o Windows mostrará a janela de permissão (UAC) e só você pode aprová-la. No modo normal ainda dá para tentar, mas desinstaladores que exigem elevação vão falhar com a mensagem "exige ADMINISTRADOR (código 740)". Arquivos e pastas não precisam de administrador (exceto `Windows\Temp`, cache do Windows Update e hibernação).

Registre que o aviso foi dado (por exemplo, criando um arquivo `.aviso-desinstalar-visto` ao lado do `server.py`) para não repetir em toda execução. Repita **sempre** o lembrete curto "programas costumam pedir administrador" quando o usuário escolher o modo normal e houver programas na lista.

## Como rodar

- Requisitos: Windows 10/11, Python 3.9+ com tcl/tk no PATH, nenhum pacote pip.
- Normal: `app\iniciar.bat`  ·  Administrador: `app\iniciar-admin.bat` (ambos aceitam `--no-browser`).
- **Pergunte ao usuário, a cada execução, se quer iniciar normal ou como administrador** e abra o `.bat` correspondente.
- Sem argumentos o servidor abre o navegador sozinho em `http://127.0.0.1:8765/`; com `--no-browser` o endereço fica em `app\server.url` e `GET <url>api/info` informa `{"admin": bool, "version": "..."}`.
- Para Claude Code existe a skill `/limpeza-de-disco` (ver `skill/SKILL.md`, instalável com `python tools/build_zip.py --install`).

## Comportamento do app que você deve conhecer

- Programas e jogos aparecem **só para desinstalar** (a pasta deles nunca é oferecida para apagar/mover). Arquivos e pastas ligados a um programa instalado são ignorados no scan.
- O resultado do scan fica só na memória e é descartado 10 minutos depois que o painel for fechado; reabrir dentro do prazo restaura as abas.
- Tudo que o painel executa fica em `limpeza.log` (pasta do app).

## Desenvolvimento

- `app/scanner.py` (heurísticas e constantes no topo), `app/server.py` (API, apagar/mover/desinstalar), `app/index.html` (painel, JS puro), `tools/build_zip.py` (gera `dist/*.zip` e instala a skill).
- Teste sem tocar nos discos reais: defina `LIMPEZA_FIXTURE` com um JSON de itens falsos (aparece um disco "T:"), `LIMPEZA_TTL_SEC` para encurtar o prazo e `LIMPEZA_PORT` para escolher a porta.
