@echo off
REM ============================================================
REM  Let WorkBuddy expose its LOCAL debugging port, so the whale
REM  pet can borrow WorkBuddy's own login to query your credits.
REM
REM  Why this is needed: WorkBuddy's login state is NOT stored
REM  anywhere readable on disk (checked: all 7 cookie DBs, localStorage,
REM  leveldb, keyblob, credential files). So instead of copying its
REM  credentials, we ask WorkBuddy itself to make the request.
REM
REM  Bound to 127.0.0.1 only -- not reachable from other machines.
REM ============================================================
chcp 65001 >nul
echo.
echo  Setting WORKBUDDY_REMOTE_DEBUGGING_PORT = 9222 ...
setx WORKBUDDY_REMOTE_DEBUGGING_PORT 9222 >nul
if %ERRORLEVEL%==0 (
    echo    Done.
) else (
    echo    FAILED.
)
echo.
echo  NEXT STEP: fully quit WorkBuddy, then open it again.
echo  After that, double-click the pet to see your credits.
echo.
echo  To undo later (in cmd):
echo    reg delete HKCU\Environment /F /V WORKBUDDY_REMOTE_DEBUGGING_PORT
echo.
pause
