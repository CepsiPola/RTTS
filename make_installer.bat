@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title RTTS - Make installer

echo RTTS - make the RTTS installer
echo.

if not exist "dist\RTTS\RTTS.exe" (
    echo [ERROR] dist\RTTS\RTTS.exe was not found. Run build.bat first.
    goto error
)

:: Find Inno Setup's compiler
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [ERROR] Inno Setup 6 is not installed.
    echo Get it free from https://jrsoftware.org/isdl.php and install it,
    echo or run this in a terminal:  winget install JRSoftware.InnoSetup
    echo Then run make_installer.bat again.
    goto error
)

echo [-] Building the installer...
if exist "installer_output\RTTS-Setup-*.exe" del /q "installer_output\RTTS-Setup-*.exe"
"%ISCC%" "RTTS.iss"
if errorlevel 1 (
    echo [ERROR] Inno Setup reported an error. Scroll up to see why.
    goto error
)

if not exist "installer_output\RTTS-Setup-*.exe" (
    echo [ERROR] The installer was not created.
    goto error
)

echo.
for %%f in ("installer_output\RTTS-Setup-*.exe") do echo [SUCCESS] Installer ready: installer_output\%%~nxf
explorer "installer_output"
goto end

:error
echo.
echo [FAILED] The installer was not made.

:end
echo.
pause >nul
