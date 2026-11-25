@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Abrindo a Limpeza de disco (o endereco aparece abaixo)
echo Feche esta janela para encerrar.
rem Argumentos repassados ao servidor, ex.: iniciar.bat --no-browser
python -I server.py %*
pause
