@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo VideoHarbor has not been set up yet.
    call "%~dp0Setup_Windows.bat"
    if errorlevel 1 exit /b 1
)
start "VideoHarbor" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0video_harbor.py"
exit /b 0
