@echo off
title Scout Ranking Bot - Albion Online
cd /d "%~dp0"

if not exist ".env" (
    echo [ERROR] No se encontro el archivo .env
    echo Copia .env.example a .env y agrega tu token.
    pause
    exit /b 1
)

if not exist "venv\" (
    echo [*] Creando entorno virtual...
    python -m venv venv
)

echo [*] Activando entorno virtual...
call venv\Scripts\activate.bat

echo [*] Instalando dependencias...
pip install -r requirements.txt --quiet

echo.
echo =========================================
echo   SCOUT RANKING BOT - ALBION ONLINE
echo =========================================
echo.
python main.py

pause
