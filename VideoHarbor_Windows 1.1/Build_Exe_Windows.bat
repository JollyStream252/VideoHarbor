@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup_Windows.bat first.
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
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoHarbor --collect-all yt_dlp %EXTRA% video_harbor.py
if errorlevel 1 goto failed
echo.
echo Built dist\VideoHarbor.exe
pause
exit /b 0
:failed
echo Build failed. You can still use Launch_Windows.bat.
pause
exit /b 1
