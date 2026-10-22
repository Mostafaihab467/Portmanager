@echo off
REM Install the optional psutil dependency (the app also works without it)
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python -m pip install -r requirements.txt
) else (
    py -3 -m pip install -r requirements.txt
)

pause
