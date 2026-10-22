@echo off
REM Launch Port Manager
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python port_manager.py
) else (
    py -3 port_manager.py
)

if errorlevel 1 pause
