@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call start.bat
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --windowed --name "GooglePhotosDedupe" app.py
echo Fertig! Ausgabe liegt unter dist\GooglePhotosDedupe\GooglePhotosDedupe.exe
pause
