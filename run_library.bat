@echo off
rem MMTX Library launcher for Windows. First run creates a local .venv and installs dependencies.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up MMTX for the first time...
    py -3 -m venv .venv || python -m venv .venv || goto :nopython
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)
start "" ".venv\Scripts\pythonw.exe" -m mmtx.library %*
exit /b 0

:nopython
echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ (tick "Add to PATH").
pause
exit /b 1

:fail
echo Dependency install failed. See the messages above.
pause
exit /b 1
