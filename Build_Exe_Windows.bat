@echo off
setlocal
cd /d "%~dp0"
title VideoHarbor 1.2 - Build Windows App
if not exist ".venv\Scripts\python.exe" (
  echo First run Setup_Windows.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import yt_dlp_ejs" >nul 2>&1
if errorlevel 1 (
  set "EXTRA="
) else (
  set "EXTRA=--collect-all yt_dlp_ejs"
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "VideoHarbor" --icon="assets\VideoHarbor.ico" --add-data "assets;assets" --collect-all yt_dlp %EXTRA% video_harbor.py
if errorlevel 1 goto failed
echo.
echo ===========================================
echo Created: dist\VideoHarbor.exe
echo ===========================================
echo You can copy or launch the EXE, or create Start menu and desktop shortcuts.
echo FFmpeg and Deno may still need to be installed separately.
pause
exit /b 0
:failed
echo.
echo Build failed; you can still run Launch_Windows.bat
pause
exit /b 1
