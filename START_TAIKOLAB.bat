@echo off
setlocal
cd /d "%~dp0"

echo.
echo Starting TaikoLab deployment...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_deployment.ps1" -Launch

set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
    echo.
    echo TaikoLab could not start.
    echo Review deployment-logs\setup.log.
    pause
)

exit /b %RESULT%