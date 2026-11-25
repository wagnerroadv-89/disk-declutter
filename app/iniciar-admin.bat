@echo off
rem Abre a Limpeza de disco como ADMINISTRADOR: pede a elevacao do Windows (UAC) e roda o iniciar.bat elevado.
rem Libera itens que exigem admin (Windows\Temp, cache do Windows Update, hibernacao).
rem Argumentos sao repassados, ex.: iniciar-admin.bat --no-browser
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0iniciar.bat' -ArgumentList '--elevado %*' -Verb RunAs"
