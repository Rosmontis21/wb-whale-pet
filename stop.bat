@echo off
REM WorkBuddy Whale Pet - stop the running pet by its PID file
setlocal
set "HERE=%~dp0"
set "PIDFILE=%HERE%run\pet.pid"

if not exist "%PIDFILE%" (
    echo [skip] No PID file found. The pet does not seem to be running.
    pause
    exit /b 0
)

set "PETPID="
for /f "usebackq delims=" %%i in ("%PIDFILE%") do set "PETPID=%%i"

if "%PETPID%"=="" (
    echo [skip] PID file is empty.
    pause
    exit /b 0
)

echo Stopping pet process PID=%PETPID% ...
taskkill /PID %PETPID% /F
del "%PIDFILE%" >nul 2>&1
echo Done.
timeout /t 2 >nul
