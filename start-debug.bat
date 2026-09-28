@echo off
REM WorkBuddy Whale Pet - start with console (useful for debugging)
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" "%HERE%wb_pet.py" %*
echo.
echo [exit code %ERRORLEVEL%]
pause
