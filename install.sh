#!/usr/bin/env bash
# Ollambo Unix Installer
set -e

echo "==================================================="
echo "       Ollambo Standalone Desktop Installer        "
echo "==================================================="
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 could not be found in PATH."
    echo "Please install Python 3.9+ from https://www.python.org/"
    exit 1
fi

python3 install.py "$@"
