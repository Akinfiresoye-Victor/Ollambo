@echo off
setlocal
echo ===================================================
echo        Ollambo Standalone Desktop Installer
echo ===================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)

python install.py %*

echo.
pause
