@echo off
TITLE Serveur Pointage Hikvision ABS
color 0A
echo ========================================================
echo   Lancement du systeme de pointage en cours...
echo ========================================================

:: Se place dans le dossier du script
cd /d "%~dp0"

:: Lance l'application Uvicorn en arriere-plan ou ouvre le navigateur
start "" http://localhost:8000

:: Lance le serveur FastAPI
python -m uvicorn src.listener_isup:app --host 0.0.0.0 --port 8000

pause