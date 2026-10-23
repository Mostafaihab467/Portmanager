@echo off
REM Create the Start Menu / Desktop shortcuts for Port Manager
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File make_shortcut.ps1 %*
pause
