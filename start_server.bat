@echo off
title Skybrush Server

set "ROOT=%~dp0"
set "SERVER_DIR=%ROOT%skybrush-server"
set "CONFIG=etc\conf\skybrush-virtual.jsonc"
set "UV=%USERPROFILE%\.local\bin\uv.exe"

echo ==================================================
echo   Skybrush Server
echo ==================================================
echo.

if not exist "%SERVER_DIR%\pyproject.toml" (
    echo [ERROR] Could not find the skybrush-server folder next to this script.
    echo         Expected: %SERVER_DIR%
    goto fail
)

if exist "%UV%" goto have_uv
where uv >nul 2>&1
if not errorlevel 1 (
    set "UV=uv"
    goto have_uv
)

echo [setup] Package manager 'uv' not found. Installing it now...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" (
    echo [ERROR] Could not install uv automatically.
    echo         Install it manually from https://docs.astral.sh/uv/ and run this again.
    goto fail
)
echo [setup] uv installed.
echo.

:have_uv
cd /d "%SERVER_DIR%"

REM A virtual environment copied from another machine points at a Python that
REM does not exist here, so verify it runs before trusting it.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "pass" >nul 2>&1
    if errorlevel 1 (
        echo [setup] Existing environment is unusable, rebuilding it...
        rmdir /s /q ".venv"
    )
)

if not exist ".venv\Scripts\skybrushd.exe" (
    echo [setup] Installing dependencies. The first run takes a few minutes...
    "%UV%" sync --no-dev
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed. Check your internet connection.
        goto fail
    )
    echo [setup] Dependencies installed.
    echo.
)

if not exist "%CONFIG%" (
    echo [ERROR] Missing configuration file: %SERVER_DIR%\%CONFIG%
    goto fail
)

echo Starting server on http://localhost:5000
echo Press Ctrl+C in this window, or run stop_server.bat, to stop it.
echo.

".venv\Scripts\skybrushd.exe" -c "%CONFIG%"

echo.
echo Server stopped.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
