@echo off
setlocal EnableDelayedExpansion
title DGX Desktop Remote — Installer

:: ─────────────────────────────────────────────────────────────────────
::  Colour palette  (requires Windows console)
::  0=Black 1=Blue 2=Green 3=Aqua 4=Red 5=Purple 6=Yellow 7=White
::  8-F = bright variants
:: ─────────────────────────────────────────────────────────────────────
color 0F

:: ── Banner ────────────────────────────────────────────────────────────
echo.
echo  [93m ██████╗  ██████╗ ██╗  ██╗    ██████╗ ███████╗███████╗██╗  ██╗████████╗ ██████╗ ██████╗ [0m
echo  [93m ██╔══██╗██╔════╝ ╚██╗██╔╝    ██╔══██╗██╔════╝██╔════╝██║ ██╔╝╚══██╔══╝██╔═══██╗██╔══██╗[0m
echo  [95m ██║  ██║██║  ███╗ ╚███╔╝     ██║  ██║█████╗  ███████╗█████╔╝    ██║   ██║   ██║██████╔╝[0m
echo  [95m ██║  ██║██║   ██║ ██╔██╗     ██║  ██║██╔══╝  ╚════██║██╔═██╗    ██║   ██║   ██║██╔═══╝ [0m
echo  [96m ██████╔╝╚██████╔╝██╔╝ ██╗    ██████╔╝███████╗███████║██║  ██╗   ██║   ╚██████╔╝██║     [0m
echo  [96m ╚═════╝  ╚═════╝ ╚═╝  ╚═╝    ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝  ╚═╝    ╚═════╝ ╚═╝     [0m
echo.
echo  [90m  ─────────────────────────────────────────────────────────────────────[0m
echo  [97m                    DGX Desktop Remote  PC Installer[0m
echo  [90m  ─────────────────────────────────────────────────────────────────────[0m
echo.

:: ── Locate install dir (same folder as this .bat) ─────────────────────
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "APP_SRC=%ROOT%\pc-application\src"
set "VENV=%ROOT%\.venv"
set "REQS=%ROOT%\pc-application\requirements.txt"

echo  [90m  Install location : [97m%ROOT%[0m
echo.

:: ── Step 1 — Find Python ──────────────────────────────────────────────
echo  [93m[1/4][0m  Locating Python 3 ...
set "PYTHON="

:: Try py launcher first (recommended on Windows)
where py >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%P"
)

:: Fall back to python / python3
if not defined PYTHON (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON set "PYTHON=%%P"
)
if not defined PYTHON (
    for /f "delims=" %%P in ('where python3 2^>nul') do if not defined PYTHON set "PYTHON=%%P"
)

if not defined PYTHON (
    echo.
    echo  [91m  ERROR: Python 3 not found.[0m
    echo  [97m  Please install Python 3.10+ from https://python.org[0m
    echo  [97m  Make sure to check "Add Python to PATH" during install.[0m
    echo.
    pause
    exit /b 1
)

:: Verify Python version >= 3.10
for /f "delims=" %%V in ('"%PYTHON%" -c "import sys; print(sys.version_info >= (3,10))" 2^>nul') do set "PY_OK=%%V"
if /i not "%PY_OK%"=="True" (
    echo.
    echo  [91m  ERROR: Python 3.10 or newer is required.[0m
    for /f "delims=" %%V in ('"%PYTHON%" --version 2^>^&1') do echo  [90m  Found: %%V[0m
    echo  [97m  Download: https://python.org/downloads[0m
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%V in ('"%PYTHON%" --version 2^>^&1') do echo  [92m  Found: %%V  →  %PYTHON%[0m
echo.

:: ── Step 2 — Create venv ──────────────────────────────────────────────
echo  [93m[2/4][0m  Creating virtual environment ...

if exist "%VENV%\Scripts\python.exe" (
    echo  [90m  .venv already exists — skipping creation.[0m
) else (
    "%PYTHON%" -m venv "%VENV%"
    if %errorlevel% neq 0 (
        echo  [91m  ERROR: Failed to create virtual environment.[0m
        pause
        exit /b 1
    )
    echo  [92m  Virtual environment created at .venv\[0m
)
echo.

set "VPYTHON=%VENV%\Scripts\python.exe"
set "VPIP=%VENV%\Scripts\pip.exe"

:: ── Step 3 — Install requirements ────────────────────────────────────
echo  [93m[3/4][0m  Installing requirements ...
echo  [90m  (This may take a minute on first install)[0m
echo.

"%VPIP%" install --upgrade pip --quiet
"%VPIP%" install -r "%REQS%"

if %errorlevel% neq 0 (
    echo.
    echo  [91m  ERROR: Package installation failed.[0m
    echo  [97m  Check your internet connection and try again.[0m
    echo.
    pause
    exit /b 1
)

echo.
echo  [92m  All packages installed successfully.[0m
echo.

:: ── Step 4 — Write LAUNCH.bat ─────────────────────────────────────────
echo  [93m[4/4][0m  Creating launcher ...

(
    echo @echo off
    echo title DGX Desktop Remote
    echo cd /d "%APP_SRC%"
    echo "%VPYTHON%" main.py %%*
) > "%ROOT%\LAUNCH.bat"

echo  [92m  LAUNCH.bat created.[0m
echo.

:: ── Optional: desktop shortcut ────────────────────────────────────────
echo  [97m  Create a desktop shortcut?  (Y/N)[0m
set /p "SHORTCUT=  > "
if /i "%SHORTCUT%"=="Y" (
    "%VPYTHON%" "%ROOT%\create_shortcuts.py"
    if %errorlevel%==0 (
        echo  [92m  Desktop shortcut created.[0m
    ) else (
        echo  [93m  Shortcut skipped (pywin32 not installed — LAUNCH.bat works instead).[0m
    )
)

:: ── Done ──────────────────────────────────────────────────────────────
echo.
echo  [90m  ─────────────────────────────────────────────────────────────────────[0m
echo  [92m  ✓  Installation complete![0m
echo  [90m  ─────────────────────────────────────────────────────────────────────[0m
echo.
echo  [97m  To launch the app, double-click  LAUNCH.bat[0m
echo  [97m  or run it from here:[0m
echo.

echo  [97m  Launch now?  (Y/N)[0m
set /p "LAUNCH=  > "
if /i "%LAUNCH%"=="Y" (
    echo.
    echo  [92m  Starting DGX Desktop Remote ...[0m
    start "" "%ROOT%\LAUNCH.bat"
)

echo.
pause
endlocal
