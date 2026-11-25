---
name: limpeza-de-disco
description: Abre o painel de Limpeza de disco (app local). O painel lista os discos do Windows, o usuário marca quais escanear e clica em "Escanear agora"; cada disco ganha uma aba com pastas/arquivos grandes parados, programas e jogos sem uso e caches, com seleção para apagar ou mover para outro disco. Use quando o usuário rodar /limpeza-de-disco ou pedir para limpar o disco C:, liberar espaço, achar o que apagar/desinstalar/mover, ou abrir o painel de limpeza.
---

# Limpeza de disco

Esta skill abre um app local (Python, só `127.0.0.1`) já pronto. O arquivo `templates.zip`, nesta mesma pasta da skill, contém o app inteiro: `server.py`, `scanner.py`, `index.html`, `iniciar.bat`, `iniciar-admin.bat`, `LEIA-ME.txt` e `VERSION`.

## Regras que valem sempre

- **Você não apaga, desinstala nem move nada.** Quem faz isso é o painel, e somente depois de o usuário marcar itens, clicar em um botão e confirmar duas vezes. Nunca clique nesses botões nem chame a API do painel (`/api/run`) em nome do usuário.
- O scan é somente leitura. Pode ser disparado pelo usuário no painel; só dispare você mesmo se ele pedir.
- Não envie o conteúdo da lista (nomes de pastas e programas do usuário) para serviços externos.

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
4. **Ver se o painel já está rodando**: se `APP\server.url` existir, faça uma requisição `GET` nessa URL; se responder 200, reaproveite. Senão apague `server.url` e suba o servidor **em segundo plano** (processo que continua depois do seu turno):
   ```powershell
   cd $APP; python -I server.py --no-browser
   ```
   Espere `server.url` aparecer (poucos segundos); ele contém o endereço (a porta padrão é 8765; se estiver ocupada o app usa a próxima livre).
5. **Abrir o painel para o usuário**: no app desktop do Claude, abra a URL no navegador embutido (`preview_start` com `url`). Se não houver navegador embutido, use `start <url>` no PowerShell.
6. **Explique em 3 linhas** o que fazer: marcar os discos, clicar em **Escanear agora** (mais de um disco = uma aba por disco), marcar *Apagar* / *Mover* (e usar "Transferir pasta para outro local" para escolher o destino) e usar os botões do rodapé; cada botão pede duas confirmações e vale só para a aba aberta.
7. Itens que exigem administrador (Windows\Temp, cache do Windows Update, hibernação) só funcionam se o servidor for iniciado com `iniciar-admin.bat`; avise isso se o usuário quiser liberar esses itens.

## Validade do scan (importante ao reabrir)

- O scan fica só na memória do servidor. **Cada abertura nova do painel começa na tela inicial (escolher discos) e exige novo scan.**
- Se o usuário fechar o painel por engano, o servidor guarda o scan por **10 minutos**; reabrindo dentro desse prazo as abas voltam com um aviso ("mantive o scan feito há N min"). Depois disso o estado é descartado.
- Por isso, ao reabrir o painel, **não** prometa que a lista anterior continua valendo: diga que ela só é mantida por 10 min após fechar e que, se os discos mudaram, é preciso escanear de novo.
- O prazo pode ser mudado com a variável de ambiente `LIMPEZA_TTL_SEC` (em segundos) antes de iniciar o servidor.

## Se o usuário pedir algo mais

- *"O que o scan procura?"* — leia `APP\LEIA-ME.txt`.
- *Ajustar os critérios* (tamanho mínimo, dias parado, lista de caches): edite as constantes no topo de `APP\scanner.py` (`DIR_MIN`, `STALE_DAYS`, `FILE_MIN`, `PROG_MIN`, `PROG_STALE_DAYS`) e reinicie o servidor.
- *Encerrar*: pare o processo do servidor (o que escuta na porta de `server.url`) quando o usuário disser que terminou.
- *Auditoria*: tudo que o painel executou fica em `APP\limpeza.log`.
