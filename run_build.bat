@echo off
setlocal

set "SONG=%~1"
if not defined SONG (
    set /p "SONG=Enter the song ID from its workspace folder: "
)

if not defined SONG (
    echo A song ID is required.
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_build.ps1" -Song "%SONG%"
set "RESULT=%ERRORLEVEL%"

echo.
pause
exit /b %RESULT%
