# Ollambo PowerShell Installer
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "       Ollambo Standalone Desktop Installer        " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "[ERROR] Python was not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.9+ from https://www.python.org/" -ForegroundColor Yellow
    exit 1
}

python install.py $args
