@echo off
title Fireworks Tech Graph - Web Console
cd /d "%~dp0"

echo.
echo   Fireworks Tech Graph - Local Web Console
echo   -----------------------------------------
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo   [ERROR] Python not found.
    echo   Install Python 3.9+ and add it to PATH.
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo   Installing dependencies ...
    python -m pip install -r web\requirements.txt
    if errorlevel 1 (
        echo   [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo   Starting server ...
echo   Browser will open: http://127.0.0.1:8777/
echo   Close this window to stop the service.
echo.
python web\server.py --port 8777

pause
