@echo off
rem Builds a standalone dist\MMTX\MMTX.exe (no Python needed on the target PC).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name MMTX ^
    --icon assets\mmtx.ico --add-data "assets;assets" ^
    --exclude-module tkinter --collect-submodules mmtx launcher.py || exit /b 1
echo.
echo Built: dist\MMTX\MMTX.exe
pause
