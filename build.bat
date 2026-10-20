@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title RTTS - Build RTTS.exe

echo RTTS - build a Windows program (RTTS.exe)
echo.

:: 1. Find Python
set "PY="
py --version >nul 2>&1
if not errorlevel 1 set "PY=py"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found. Run installer.bat first.
    goto error
)

:: 2. Check the files and libraries RTTS needs
if not exist "icon.ico" (
    echo [ERROR] icon.ico is missing from this folder.
    goto error
)
if not exist "assets\fonts\SpaceMono-Regular.ttf" (
    echo [ERROR] assets\fonts is missing from this folder.
    goto error
)
%PY% -c "import numpy, sounddevice, pynput, kokoro_onnx, tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] RTTS's libraries are not installed. Run installer.bat first.
    goto error
)

:: 3. Install the builder
echo [-] Installing PyInstaller...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] Could not install PyInstaller. Check your internet connection.
    goto error
)

:: 4. Bundle the data files of the voice engine libraries (only those that are installed)
set "COLLECT="
for %%m in (kokoro_onnx espeakng_loader phonemizer onnxruntime language_tags colorlog) do (
    %PY% -c "import %%m" >nul 2>&1
    if not errorlevel 1 set "COLLECT=!COLLECT! --collect-all %%m"
)

:: 5. Build
echo.
echo [-] Building. This takes a few minutes...
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --name RTTS --icon "icon.ico" --add-data "icon.ico;." --add-data "assets\fonts;assets\fonts" --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 !COLLECT! RTTS.py
if errorlevel 1 (
    echo [ERROR] The build failed. Scroll up to see why.
    goto error
)

if not exist "dist\RTTS\RTTS.exe" (
    echo [ERROR] The build finished but dist\RTTS\RTTS.exe was not created.
    goto error
)

:: remove PyInstaller's scratch files so the unfinished build\RTTS\RTTS.exe can't be run by mistake
if exist "build" rmdir /s /q "build"
if exist "RTTS.spec" del /q "RTTS.spec"

echo.
echo [SUCCESS] Built: dist\RTTS\RTTS.exe
echo Run RTTS.exe from the dist\RTTS folder that is about to open.
echo Keep that whole folder together. Copy or zip the folder to share it.
echo The voice files download on first run, next to RTTS.exe.
explorer "dist\RTTS"
goto end

:error
echo.
echo [FAILED] Build did not finish.

:end
echo.
pause >nul
