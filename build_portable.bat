@echo off
REM Build the standalone Windows app: dist\PortManager\PortManager.exe
REM Needs PyInstaller once:  python -m pip install pyinstaller
cd /d "%~dp0"

set ARGS=--noconfirm --clean --windowed --name PortManager ^
 --icon "%~dp0assets\icon.ico" --add-data "%~dp0assets;assets" ^
 --version-file "%~dp0version_info.txt" ^
 --workpath build --specpath build ^
 "%~dp0port_manager.py"

where python >nul 2>nul
if %errorlevel%==0 (
    python -m PyInstaller %ARGS%
) else (
    py -3 -m PyInstaller %ARGS%
)

if errorlevel 1 (
    echo.
    echo Build failed - install PyInstaller with:  python -m pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo Built  %~dp0dist\PortManager\PortManager.exe
echo Run install_shortcut.bat afterwards to point the shortcuts at it.
pause
