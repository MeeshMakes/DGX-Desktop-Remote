@echo off
title DGX Desktop Remote
cd /d "%~dp0pc-application\src"

:: Use project venv if it exists, otherwise fall back to system Python
set "VPYTHON=%~dp0.venv\Scripts\pythonw.exe"
set "VPYTHON_CON=%~dp0.venv\Scripts\python.exe"

if exist "%VPYTHON%" (
    start "" "%VPYTHON%" main.py %*
) else if exist "%VPYTHON_CON%" (
    start "" "%VPYTHON_CON%" main.py %*
) else (
    echo.
    echo  Run INSTALL.bat first to set up the application.
    echo.
    pause
)
