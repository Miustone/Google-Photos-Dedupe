@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Erstelle virtuelle Python-Umgebung...
    python -m venv .venv
    if errorlevel 1 (
        echo FEHLER: Python 3 wurde nicht gefunden. Bitte Python 3.10+ von python.org installieren.
        pause
        exit /b 1
    )
    echo [2/3] Installiere Abhaengigkeiten...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    echo [3/3] Installiere Playwright Chromium (falls System-Chrome nicht genutzt wird)...
    ".venv\Scripts\playwright.exe" install chromium
)

echo Starte Google Fotos Smart-Duplikat-Finder...
start "" ".venv\Scripts\pythonw.exe" app.py
