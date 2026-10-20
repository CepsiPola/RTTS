@echo off
setlocal EnableExtensions
title Roblox Text To Speech (RTTS) - Installer

echo Roblox Text To Speech (RTTS) - Setup
echo.

:: 1. Find Python (the "py" launcher first, then plain "python")
set "PY="
py --version >nul 2>&1
if not errorlevel 1 set "PY=py"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] Python is not installed or not detected!
    echo Please download Python 3.10 or newer from python.org and make sure
    echo to tick "Add python.exe to PATH" during installation.
    goto error
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Your Python is too old. RTTS needs Python 3.10 or newer.
    echo Please install a newer version from python.org.
    goto error
)

echo [OK] Python is detected.
echo.

:: 2. Check if pip is available
echo Checking package manager...
%PY% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [-] pip is missing. Attempting to install pip...
    %PY% -m ensurepip --default-pip
    if errorlevel 1 (
        echo [ERROR] Could not install pip automatically. Please reinstall Python.
        goto error
    )
) else (
    echo [OK] pip is ready. Updating pip...
    %PY% -m pip install --upgrade pip --quiet
)
echo.

:: 3. Check and install required packages (pip name, then import name)
echo Checking required libraries...
echo.

call :ensure numpy numpy || goto error
call :ensure sounddevice sounddevice || goto error
call :ensure pynput pynput || goto error
call :ensure kokoro-onnx kokoro_onnx || goto error

:: tkinter ships with Python itself (tick "tcl/tk" in the Python installer)
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python's tkinter component is missing.
    echo Re-run the Python installer, choose Modify, and tick "tcl/tk and IDLE".
    goto error
)

echo.
echo [SUCCESS] All dependencies are ready for RTTS!
echo Start the app with start.bat
goto end

:ensure
%PY% -c "import %~2" >nul 2>&1
if errorlevel 1 (
    echo [-] Installing: %~1 ...
    %PY% -m pip install %~1
    if errorlevel 1 (
        echo [ERROR] Failed to install %~1. Check your internet connection.
        exit /b 1
    )
) else (
    echo [+] %~1 is already installed.
)
exit /b 0

:error
echo.
echo [FAILED] Setup encountered an error. Please resolve the
echo issue above and run installer.bat again.

:end
echo.
pause >nul
