@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo VideoHarbor 1.2 requires first-time setup.
    call "%~dp0Setup_Windows.bat"
    if errorlevel 1 exit /b 1
)
start "VideoHarbor 1.2" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0video_harbor.py"
exit /b 0
