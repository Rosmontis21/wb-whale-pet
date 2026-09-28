@echo off
REM ============================================================
REM  Whale Pet - sound picker (GUI)
REM  Pick a sound for each action, with preview.
REM ============================================================
chcp 65001 >nul
set "HERE=%~dp0"
set "PY=pythonw"
if exist "%HERE%.venv\Scripts\pythonw.exe" set "PY=%HERE%.venv\Scripts\pythonw.exe"
start "" "%PY%" "%HERE%soundpicker.py"
