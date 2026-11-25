@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Abrindo a Limpeza de disco (o endereco aparece abaixo e o navegador abre sozinho)
echo Feche esta janela para encerrar.
python -I server.py
pause
