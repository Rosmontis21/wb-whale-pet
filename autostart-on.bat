@echo off
REM Turn ON "start with Windows" for the whale pet
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" "%HERE%autostart.py" on
echo.
pause
