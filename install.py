#!/usr/bin/env python3
r"""
Ollambo Installer & Desktop Packager
===================================
One-command installation script to build and install Ollambo as a standalone
desktop application on Windows, macOS, or Linux.

Features:
  - Interactive location prompt with native File Explorer folder picker.
  - Leaves the working directory completely clean (cleans up temporary build files).
  - Installs to standard user application directory or custom folder.
  - Automatically creates Desktop and Start Menu shortcuts on Windows.

Usage:
    python install.py                   # Interactive install (prompts for location)
    python install.py --dir "C:\Apps"   # Install directly to specified location
    python install.py --onefile         # Package as single-file executable
    python install.py --dev             # Install in editable developer mode
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def print_step(msg: str) -> None:
    print(f"\n\033[1;36m==>\033[0m \033[1m{msg}\033[0m")


def print_success(msg: str) -> None:
    print(f"\033[1;32m✔ {msg}\033[0m")


def print_error(msg: str) -> None:
    print(f"\033[1;31m✖ Error: {msg}\033[0m", file=sys.stderr)


def check_python_version() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 9):
        print_error(f"Python 3.9+ is required. Found Python {major}.{minor}.")
        sys.exit(1)
    print_success(f"Python {major}.{minor}.{sys.version_info[2]} detected.")


def get_default_install_dir() -> Path:
    """Return standard user application install directory outside working directory."""
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "Programs" / "Ollambo"
        return Path.home() / "AppData" / "Local" / "Programs" / "Ollambo"
    elif sys.platform == "darwin":
        return Path.home() / "Applications" / "Ollambo"
    else:
        return Path.home() / ".local" / "share" / "ollambo"


def pick_folder_gui(initial_dir: Path | None = None) -> Path | None:
    """Open native File Explorer folder selection dialog."""
    # 1. Try tkinter (standard in most Python installs)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(
            title="Select Ollambo Installation Folder",
            initialdir=str(initial_dir) if initial_dir else None,
        )
        root.destroy()
        if folder:
            return Path(folder)
    except Exception:
        pass

    # 2. Try PowerShell System.Windows.Forms on Windows
    if sys.platform == "win32":
        try:
            init_str = str(initial_dir) if initial_dir else ""
            ps_script = (
                "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select Ollambo Installation Folder'; "
                f"$f.SelectedPath = '{init_str}'; "
                "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
            )
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True)
            chosen = res.stdout.strip()
            if chosen and Path(chosen).exists():
                return Path(chosen)
        except Exception:
            pass

    return None


def prompt_installation_directory(default_dir: Path) -> Path:
    """Ask user for target installation folder with optional File Explorer browse dialog."""
    print(f"\nDefault installation directory:")
    print(f"  \033[1;33m{default_dir}\033[0m\n")
    print("Options:")
    print("  • Press [Enter] to use the default directory above")
    print("  • Press [B] to browse and choose a folder in File Explorer")
    print("  • Or type a custom directory path below\n")

    while True:
        try:
            choice = input("Installation path [Enter=Default, B=Browse]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nInstallation cancelled.")
            sys.exit(0)

        if not choice:
            return default_dir

        if choice.lower() == "b":
            print("Opening File Explorer folder picker…")
            picked = pick_folder_gui(initial_dir=default_dir.parent)
            if picked:
                if picked.name.lower() != "ollambo":
                    picked = picked / "Ollambo"
                print(f"Selected: {picked}")
                return picked
            else:
                print("No folder selected. Using default or try again.")
                continue

        # Custom path entered
        custom_path = Path(choice).expanduser().resolve()
        if custom_path.name.lower() != "ollambo" and not custom_path.suffix:
            custom_path = custom_path / "Ollambo"
        return custom_path


def install_dependencies(include_dev: bool = False) -> None:
    print_step("Installing required dependencies...")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "wheel"]
    subprocess.check_call(cmd)

    req_file = ROOT_DIR / "requirements.txt"
    if req_file.exists():
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(req_file)]
        subprocess.check_call(cmd)

    if not include_dev:
        print_step("Ensuring PyInstaller is installed...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0.0"])

    print_success("Dependencies installed successfully.")


def _create_windows_lnk(shortcut_path: Path, target_exe: Path, icon_path: Path) -> None:
    ps_script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{shortcut_path}'); "
        f"$s.TargetPath = '{target_exe}'; "
        f"$s.WorkingDirectory = '{target_exe.parent}'; "
        f"$s.IconLocation = '{icon_path}'; "
        f"$s.Description = 'Ollambo Local AI Coding Assistant'; "
        f"$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True, capture_output=True)


def create_shortcuts(target_exe: Path) -> None:
    """Create Windows Desktop and Start Menu shortcuts."""
    if sys.platform != "win32":
        return

    icon_path = target_exe.parent / "Favicon.ico"
    if not icon_path.exists():
        icon_path = ROOT_DIR / "Favicon.ico"
    if not icon_path.exists():
        icon_path = target_exe

    # Desktop shortcut
    try:
        desktop_dir = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        if desktop_dir.exists():
            desktop_lnk = desktop_dir / "Ollambo.lnk"
            _create_windows_lnk(desktop_lnk, target_exe, icon_path)
            print_success(f"Desktop shortcut created: {desktop_lnk}")
    except Exception as e:
        print(f"Note: Could not create desktop shortcut ({e}).")

    # Start Menu shortcut
    try:
        appdata = os.environ.get("APPDATA")
        if appdata:
            start_menu_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if start_menu_dir.exists():
                start_lnk = start_menu_dir / "Ollambo.lnk"
                _create_windows_lnk(start_lnk, target_exe, icon_path)
                print_success(f"Start Menu shortcut created: {start_lnk}")
    except Exception as e:
        pass


def build_and_install(install_dir: Path, onefile: bool = False) -> Path:
    print_step("Building standalone desktop application...")

    spec_file = ROOT_DIR / "ollambo.spec"
    dist_dir = ROOT_DIR / "dist"
    build_dir = ROOT_DIR / "build"

    # Clean any stale build folders before compiling
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)

    if onefile:
        icon_arg = []
        for ico in ("Favicon.ico", "favicon.ico", "icon.png"):
            if (ROOT_DIR / ico).exists():
                icon_arg = [f"--icon={ROOT_DIR / ico}"]
                break

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--onefile",
            "--name=Ollambo",
            *icon_arg,
            f"--add-data={ROOT_DIR / 'Favicon.ico'}{os.pathsep}.",
            f"--add-data={ROOT_DIR / 'icon.png'}{os.pathsep}.",
            str(ROOT_DIR / "main.py"),
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(spec_file if spec_file.exists() else ROOT_DIR / "main.py"),
        ]

    subprocess.check_call(cmd, cwd=str(ROOT_DIR))

    is_win = sys.platform == "win32"
    exe_name = "Ollambo.exe" if is_win else "Ollambo"

    # Locate generated output in temporary dist folder
    if onefile:
        built_binary = dist_dir / exe_name
    else:
        built_binary = dist_dir / "Ollambo" / exe_name

    if not built_binary.exists():
        found = list(dist_dir.rglob(exe_name))
        if found:
            built_binary = found[0]
        else:
            print_error("Build finished but executable was not found in dist/.")
            sys.exit(1)

    print_step(f"Installing Ollambo into: {install_dir}...")
    install_dir.mkdir(parents=True, exist_ok=True)

    final_exe = install_dir / exe_name

    if onefile:
        shutil.copy2(built_binary, final_exe)
    else:
        built_app_dir = built_binary.parent
        # Copy directory contents into install_dir
        shutil.copytree(built_app_dir, install_dir, dirs_exist_ok=True)

    # Copy icons to install directory for desktop shortcuts
    for asset in ("Favicon.ico", "icon.png"):
        src = ROOT_DIR / asset
        if src.exists() and not (install_dir / asset).exists():
            shutil.copy2(src, install_dir / asset)

    # CLEAN UP WORKING DIRECTORY: remove temporary build/ and dist/
    print_step("Cleaning up temporary build artifacts from working directory...")
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)
    print_success("Working directory cleaned. No extra build files left behind.")

    print_success(f"Ollambo installed successfully to:\n    {final_exe}")
    return final_exe


def install_dev_mode() -> None:
    print_step("Installing Ollambo in developer editable mode...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(ROOT_DIR)])
    print_success("Ollambo installed in editable mode! You can now run `ollambo` or `python main.py` anywhere.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install and package Ollambo standalone desktop application.")
    parser.add_argument("--dir", type=str, default="", help="Custom installation directory path.")
    parser.add_argument("--onefile", action="store_true", help="Package into a single executable file.")
    parser.add_argument("--dev", action="store_true", help="Install in editable Python package mode (pip install -e .).")
    parser.add_argument("--no-shortcut", action="store_true", help="Do not create desktop shortcuts on Windows.")
    args = parser.parse_args()

    print("\n" + "=" * 55)
    print("        🦙 Ollambo Desktop Installer & Packager        ")
    print("=" * 55)

    check_python_version()

    if args.dev:
        install_dependencies(include_dev=True)
        install_dev_mode()
        print("\n" + "=" * 55)
        print("                  🎉 All Done!                         ")
        print("=" * 55)
        print("To launch Ollambo:")
        print("  Run in terminal:  ollambo   (or python main.py)")
        print("=" * 55 + "\n")
        return

    # Determine installation directory
    if args.dir:
        install_dir = Path(args.dir).expanduser().resolve()
        if install_dir.name.lower() != "ollambo" and not install_dir.suffix:
            install_dir = install_dir / "Ollambo"
    else:
        default_dir = get_default_install_dir()
        install_dir = prompt_installation_directory(default_dir)

    print(f"\nTarget installation location: \033[1;32m{install_dir}\033[0m")

    install_dependencies(include_dev=False)
    final_exe = build_and_install(install_dir=install_dir, onefile=args.onefile)

    if not args.no_shortcut and sys.platform == "win32":
        create_shortcuts(final_exe)

    print("\n" + "=" * 55)
    print("                  🎉 All Done!                         ")
    print("=" * 55)
    print(f"Ollambo is installed at:")
    print(f"  {final_exe}")
    print("\nTo launch Ollambo:")
    print("  • Double-click the Desktop shortcut 'Ollambo'")
    if sys.platform == "win32":
        print("  • Or find 'Ollambo' in the Windows Start Menu")
    print(f"  • Or run directly: {final_exe}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
