---
name: limpeza-de-disco
description: Abre o painel de Limpeza de disco (app local). Toda vez que roda, pergunta se o painel deve iniciar normal ou como administrador e abre o .bat correspondente. O painel lista os discos do Windows, o usuário marca quais escanear e clica em "Escanear agora"; cada disco ganha uma aba com pastas/arquivos grandes parados, programas e jogos sem uso e caches, com seleção para apagar ou mover para outro disco. Use quando o usuário rodar /limpeza-de-disco ou pedir para limpar o disco C:, liberar espaço, achar o que apagar/desinstalar/mover, ou abrir o painel de limpeza.
---

# Limpeza de disco

Esta skill abre um app local (Python, só `127.0.0.1`) já pronto. O arquivo `templates.zip`, nesta mesma pasta da skill, contém o app inteiro: `server.py`, `scanner.py`, `index.html`, `iniciar.bat`, `iniciar-admin.bat`, `LEIA-ME.txt` e `VERSION`.

## Regras que valem sempre

- **Você não apaga, desinstala nem move nada.** Quem faz isso é o painel, e somente depois de o usuário marcar itens, clicar em um botão e confirmar duas vezes. Nunca clique nesses botões nem chame a API do painel (`/api/run`) em nome do usuário.
- O scan é somente leitura. Pode ser disparado pelo usuário no painel; só dispare você mesmo se ele pedir.
- Não envie o conteúdo da lista (nomes de pastas e programas do usuário) para serviços externos.
- **Pergunte o modo de início TODA vez que a skill rodar** (passo 4), mesmo que já tenha perguntado antes. Nunca escolha o modo por conta própria.
- **Na primeira execução, avise que desinstalar programas pode exigir administrador** (passo 4). Isso vale ao rodar a skill dentro do console do Claude Code ou de outra IA; o mesmo aviso está no `README.md`/`AGENTS.md` do repositório e dentro do painel.
- Você **não pode aprovar** a janela de permissão do Windows (UAC) que aparece no modo administrador: é o usuário quem clica em "Sim".

## Passo a passo

1. **Pasta de trabalho**: `%USERPROFILE%\LimpezaDisco` (use outra se o usuário indicar). Guarde em `APP`.
2. **Instalar/atualizar os arquivos** (só se `APP\server.py` não existir ou `APP\VERSION` for diferente da versão dentro do zip). O zip está em `%USERPROFILE%\.claude\skills\limpeza-de-disco\templates.zip`:
   ```powershell
   $APP = Join-Path $env:USERPROFILE 'LimpezaDisco'
   New-Item -ItemType Directory -Force $APP | Out-Null
   Expand-Archive -LiteralPath (Join-Path $env:USERPROFILE '.claude\skills\limpeza-de-disco\templates.zip') -DestinationPath $APP -Force
   ```
   O `limpeza.log` do usuário fica na pasta e não está no zip, então não é sobrescrito.
3. **Checar Python com tkinter**: `python -I -c "import tkinter, winreg; print('ok')"`. Se falhar, diga ao usuário que precisa instalar o Python oficial (python.org) com a opção "tcl/tk" e pare.
4. **Aviso da primeira execução + perguntar como iniciar.**
   - **Aviso (só na PRIMEIRA vez que a skill roda neste computador)**: se o arquivo `APP\.aviso-desinstalar-visto` **não existir**, diga ao usuário, em texto claro e **antes** de perguntar o modo, o aviso abaixo; depois crie esse arquivo (vazio) para não repetir:
     > ⚠️ **Desinstalar programas pode exigir administrador.** Os programas e jogos aparecem no painel **só para desinstalar** (a pasta deles nunca é apagada nem movida). Os instalados para todos os usuários (a maioria, em `Program Files`) costumam pedir **administrador**; o painel marca esses com “desinstalar pode exigir administrador”. Para evitar erro, inicie **como administrador** — o Windows mostra uma janela de permissão (UAC) que só você pode aprovar. No modo normal ainda dá para tentar, mas os desinstaladores que exigirem elevação vão falhar (“exige ADMINISTRADOR, código 740”). Arquivos e pastas não precisam de administrador (exceto `Windows\Temp`, cache do Windows Update e hibernação).
   - **Pergunta (a cada execução)** — use a ferramenta de perguntas, `AskUserQuestion`, ou pergunte no chat se ela não existir:
     - **Normal** *(padrão sugerido)*: roda sem privilégios. Itens que exigem administrador (`Windows\Temp`, cache do Windows Update, hibernação) aparecem desabilitados, e desinstalar programas "de todos os usuários" pode falhar.
     - **Como administrador** *(recomendado se for desinstalar programas)*: libera esses itens e os desinstaladores; o Windows mostra uma janela de permissão (UAC) que o usuário precisa aprovar.
     Guarde a resposta em `MODO` (`normal` ou `admin`). Se escolher **normal** e a lista tiver programas, lembre em uma frase que programas costumam pedir administrador.
5. **Ver se já há um painel rodando**: se `APP\server.url` existir, leia a URL e faça `GET <url>api/info` (sem token; devolve `{"app","admin","version"}`).
   - Respondeu e `admin` **coincide** com `MODO` (e a versão é a mesma de `APP\VERSION`): **reaproveite** (mantém o scan guardado, se ainda estiver dentro dos 10 min) e pule para o passo 7.
   - Respondeu mas o modo **não coincide** (ou a versão é antiga): é preciso reiniciar. Pare o processo que escuta na porta da URL (`Get-NetTCPConnection -LocalPort <porta> -State Listen` → `Stop-Process -Id <pid> -Force`). Se der "acesso negado" (servidor que está elevado), peça ao usuário para **fechar a janela do servidor** (a janela preta "Limpeza de disco") e aguarde a confirmação; depois siga.
   - Não respondeu: apague `APP\server.url` e siga.
6. **Abrir o .bat do modo escolhido** (os dois aceitam `--no-browser`, porque quem abre o painel é a skill, no passo 7). Use `Start-Process`, que abre o servidor numa janela própria que o usuário pode fechar para encerrar:
   ```powershell
   # MODO = normal
   Start-Process -FilePath "$APP\iniciar.bat" -ArgumentList '--no-browser' -WorkingDirectory $APP
   # MODO = admin  (aparece o UAC: peça ao usuário para clicar em "Sim")
   Start-Process -FilePath "$APP\iniciar-admin.bat" -ArgumentList '--no-browser' -WorkingDirectory $APP
   ```
   No modo admin, avise **antes** que vai aparecer a janela de permissão do Windows. Espere o `APP\server.url` aparecer (até ~60 s; no admin depende do usuário aprovar). Ele contém o endereço (porta padrão 8765; se estiver ocupada o app usa a próxima livre).
   - Confirme com `GET <url>api/info` que `admin` bate com `MODO`. Se escolheu admin e o servidor subiu sem admin (ou nada subiu), o usuário provavelmente recusou o UAC: diga isso e ofereça tentar de novo ou seguir no modo normal.
7. **Abrir o painel para o usuário**: no app desktop do Claude, abra a URL no navegador embutido (`preview_start` com `url`). Se não houver navegador embutido, use `start <url>` no PowerShell.
8. **Explique em 3 linhas** o que fazer: marcar os discos, clicar em **Escanear agora** (mais de um disco = uma aba por disco), marcar *Apagar / Desinstalar* e *Mover* (e usar "Transferir pasta para outro local" para escolher o destino) e usar os botões do rodapé, que estão em **três grupos**: *Arquivos e pastas* (apagar todos/selecionados, mover, apagar e mover), *Programas — só desinstalar* (desinstalar todos/selecionados) e *Arquivos + programas* (os dois juntos). **Programas nunca têm a pasta apagada ou movida: só são desinstalados.** Cada botão pede duas confirmações e vale só para a aba aberta. Avise também que **o caminho de cada item é um link: clicar nele abre o Explorer do Windows já mostrando o arquivo/pasta** (para conferir antes de apagar, sem copiar e colar). Diga em que modo o painel está rodando (normal/administrador).

## Validade do scan (importante ao reabrir)

- O scan fica só na memória do servidor. **Cada abertura nova do painel começa na tela inicial (escolher discos) e exige novo scan.**
- Se o usuário fechar o painel por engano, o servidor guarda o scan por **10 minutos**; reabrindo dentro desse prazo as abas voltam com um aviso ("mantive o scan feito há N min"). Depois disso o estado é descartado.
- Por isso, ao reabrir o painel, **não** prometa que a lista anterior continua valendo: diga que ela só é mantida por 10 min após fechar e que, se os discos mudaram, é preciso escanear de novo.
- Trocar de modo (normal ↔ administrador) reinicia o servidor e **descarta o scan guardado**; avise o usuário se isso acontecer.
- O prazo pode ser mudado com a variável de ambiente `LIMPEZA_TTL_SEC` (em segundos) antes de iniciar o servidor.

## Se o usuário pedir algo mais

- *"O que o scan procura?"* — leia `APP\LEIA-ME.txt`.
- *Ajustar os critérios* (tamanho mínimo, dias parado, lista de caches): edite as constantes no topo de `APP\scanner.py` (`DIR_MIN`, `STALE_DAYS`, `FILE_MIN`, `PROG_MIN`, `PROG_STALE_DAYS`) e reinicie o servidor.
- *Encerrar*: pare o processo do servidor (o que escuta na porta de `server.url`) ou peça ao usuário que feche a janela do servidor; se o servidor estiver como administrador, só o usuário consegue fechá-lo.
- *Auditoria*: tudo que o painel executou fica em `APP\limpeza.log`.
