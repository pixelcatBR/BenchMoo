@echo off
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python nao encontrado. Instale em python.org e marque "Add Python to PATH".
    pause
    exit /b
)

if not exist ".instalado" (
    echo Primeira execucao: instalando dependencias...
    python -m pip install PyQt5 psutil
    if errorlevel 1 (
        echo Falha ao instalar as dependencias.
        pause
        exit /b
    )
    echo ok> .instalado
)

python main.py
if errorlevel 1 pause
