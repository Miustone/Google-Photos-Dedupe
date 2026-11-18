@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Erstelle virtuelle Python-Umgebung...
    python -m venv .venv
    if errorlevel 1 (
        echo FEHLER: Python 3 wurde nicht gefunden. Bitte Python 3.10+ von python.org installieren.
        pause
        exit /b 1
    )
    echo [2/2] Installiere Abhaengigkeiten...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo Starte Google Fotos Smart-Duplikat-Finder (nutzt Chrome oder Edge)...
start "" ".venv\Scripts\pythonw.exe" app.py
