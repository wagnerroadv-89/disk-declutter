@echo off
rem Abre a Limpeza de disco como ADMINISTRADOR (libera itens que exigem admin: cache do Windows Update, Windows\Temp e hibernacao).
powershell -NoProfile -Command "Start-Process cmd -Verb RunAs -ArgumentList '/k cd /d \"%~dp0\" && python -I server.py'"
