@echo off
rem ============================================================
rem  Launch AI Translator WITH a console window, so that any
rem  startup error / traceback is visible instead of vanishing.
rem  Use this when the app closes immediately or behaves oddly.
rem ============================================================
setlocal
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
  goto :done
)

where python.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python.exe not found in PATH.
  echo Install Python, or run:  "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" main.py
  pause
  exit /b 1
)

python.exe "%~dp0main.py"

:done
echo.
echo ---- App exited with code %errorlevel% ----
pause
