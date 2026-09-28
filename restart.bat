@echo off
REM Restart the whale pet: stop the running one, then launch the new code.
chcp 65001 >nul
setlocal
set "HERE=%~dp0"

echo Stopping old pet ...
if exist "%HERE%run\pet.pid" (
    set "PETPID="
    for /f "usebackq delims=" %%i in ("%HERE%run\pet.pid") do set "PETPID=%%i"
    if defined PETPID (
        taskkill /PID %PETPID% /F >nul 2>&1
        echo   killed PID %PETPID%
    )
    del "%HERE%run\pet.pid" >nul 2>&1
) else (
    echo   no PID file, nothing running
)

echo Starting new pet ...
start "" wscript.exe "%HERE%start.vbs"
echo Done. The pet should appear in a second or two.
timeout /t 3 >nul
