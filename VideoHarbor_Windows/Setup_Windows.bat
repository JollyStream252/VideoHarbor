@echo off
setlocal
cd /d "%~dp0"
title VideoHarbor 1.2 - First-time setup
echo.
echo ======================================================
echo   VideoHarbor 1.2 - Windows First-time Setup
echo ======================================================
echo.
where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    goto create_env
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    goto create_env
)
echo Python 3 was not found.
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Or run this command in Windows Terminal:
echo     winget install -e --id Python.Python.3.13
echo Then restart this setup script.
pause
exit /b 1
:create_env
echo [1/4] Creating private Python environment...
if not exist ".venv\Scripts\python.exe" (
    %PYTHON% -m venv .venv
    if errorlevel 1 goto failed
)
echo.
echo [2/4] Installing yt-dlp and thumbnail support...
".venv\Scripts\python.exe" -m pip install --upgrade "yt-dlp[default]" Pillow
if errorlevel 1 goto failed
echo.
echo [3/4] Checking FFmpeg for video/audio merging...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing FFmpeg using winget...
        winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    ) else (
        echo FFmpeg not found. Get it from https://ffmpeg.org/download.html
    )
) else (
    echo FFmpeg is already available.
)
echo.
echo [4/4] Checking Deno for YouTube compatibility...
where deno >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing Deno using winget...
        winget install -e --id DenoLand.Deno --accept-source-agreements --accept-package-agreements
    ) else (
        echo Deno not found. Install from https://docs.deno.com/runtime/getting_started/installation/
    )
) else (
    echo Deno is already available.
)
echo.
echo Setup finished. Close and reopen your Windows Terminal if FFmpeg or Deno
 echo was just installed, so the updated PATH becomes available.
echo Double-click Launch_Windows.bat to open VideoHarbor.
pause
exit /b 0
:failed
echo.
echo Setup failed. Check that Python works and you have Internet access.
pause
exit /b 1
