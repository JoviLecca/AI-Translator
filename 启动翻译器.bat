@echo off
rem ============================================================
rem  Launch AI Translator directly from source code.
rem  NO build / NO PyInstaller needed - just double-click this file.
rem  Code changes take effect the next time you launch.
rem ============================================================
setlocal
cd /d "%~dp0"

rem 1) Prefer a project-local virtualenv, if one exists.
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
  exit /b 0
)

rem 2) Use pythonw.exe from PATH (pythonw = no console window).
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0main.py"
  exit /b 0
)

rem 3) Fallback: known interpreter location on this machine.
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" (
  start "" "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" "%~dp0main.py"
  exit /b 0
)

echo [ERROR] pythonw.exe not found.
echo Install Python, or edit this file to point at your pythonw.exe.
pause
exit /b 1
