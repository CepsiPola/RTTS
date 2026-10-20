@echo off
cd /d "%~dp0"
where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw "%~dp0RTTS.py"
    exit /b 0
)
where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "%~dp0RTTS.py"
    exit /b 0
)
echo Python was not found. Run installer.bat first.
pause
