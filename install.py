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
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
MANIFEST_FILENAME = ".ollambo-manifest.json"

# Configure UTF-8 encoding on Windows to prevent charmap / cp1252 UnicodeEncodeError
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_step(msg: str) -> None:
    try:
        print(f"\n\033[1;36m==>\033[0m \033[1m{msg}\033[0m")
    except UnicodeEncodeError:
        print(f"\n==> {msg}")


def print_success(msg: str) -> None:
    try:
        print(f"\033[1;32m✔ {msg}\033[0m")
    except UnicodeEncodeError:
        print(f"\033[1;32m[OK] {msg}\033[0m")


def print_error(msg: str) -> None:
    try:
        print(f"\033[1;31m✖ Error: {msg}\033[0m", file=sys.stderr)
    except UnicodeEncodeError:
        print(f"\033[1;31m[ERROR] {msg}\033[0m", file=sys.stderr)


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


def is_ollambo_running() -> bool:
    """Check if an Ollambo process is currently active."""
    exe_target = "Ollambo.exe" if sys.platform == "win32" else "Ollambo"
    # 1. Check with psutil if available
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info.get("name") or ""
                if name.lower() == exe_target.lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception:
        pass

    # 2. Check using OS commands
    if sys.platform == "win32":
        try:
            output = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_target}", "/NH"],
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return exe_target.lower() in output.lower() and "no tasks" not in output.lower()
        except Exception:
            return False
    else:
        try:
            output = subprocess.check_output(["pgrep", "-f", exe_target], text=True)
            return bool(output.strip())
        except Exception:
            return False


def terminate_ollambo_process() -> bool:
    """Attempt to terminate any running Ollambo instances."""
    exe_target = "Ollambo.exe" if sys.platform == "win32" else "Ollambo"
    try:
        import psutil
        terminated = False
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info.get("name", "").lower() == exe_target.lower():
                    proc.terminate()
                    proc.wait(timeout=3)
                    terminated = True
            except Exception:
                pass
        if terminated:
            return True
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            res = subprocess.run(["taskkill", "/IM", exe_target, "/F"], capture_output=True, text=True)
            return res.returncode == 0
        except Exception:
            return False
    else:
        try:
            res = subprocess.run(["pkill", "-9", "-f", exe_target], capture_output=True, text=True)
            return res.returncode == 0
        except Exception:
            return False


def ensure_ollambo_not_running(allow_prompt: bool = True) -> bool:
    """Ensure that no running Ollambo process is locking files during installation/update."""
    while is_ollambo_running():
        print("\n\033[1;33m⚠️  Warning: Ollambo is currently running!\033[0m")
        print("To avoid file permission lockups and corrupted application files,")
        print("Ollambo must be closed before installing or updating.")

        if not allow_prompt:
            print("Terminating running Ollambo process...")
            terminate_ollambo_process()
            import time
            time.sleep(1)
            if not is_ollambo_running():
                print_success("Ollambo process terminated.")
                return True
            print_error("Failed to automatically terminate Ollambo process.")
            return False

        print("\nOptions:")
        print("  • Press [K] to automatically terminate the running Ollambo process")
        print("  • Press [Enter] to retry after closing Ollambo manually")
        print("  • Press [Q] to cancel installation\n")

        try:
            choice = input("Choice [K=Kill, Enter=Retry, Q=Cancel]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nInstallation cancelled.")
            sys.exit(0)

        if choice == "q":
            print("Installation cancelled. No changes were made.")
            sys.exit(0)
        elif choice == "k":
            print("Terminating Ollambo process...")
            terminate_ollambo_process()
            import time
            time.sleep(1)
            if not is_ollambo_running():
                print_success("Ollambo process terminated successfully.")
                break
            else:
                print_error("Could not terminate Ollambo process automatically. Please close it in Task Manager.")
        else:
            # Re-check loop
            pass

    return True


def get_source_identifier() -> dict:
    """Compute build identifier based on version, git commit, and source fingerprint."""
    version = "1.0.0"
    setup_file = ROOT_DIR / "setup.py"
    if setup_file.exists():
        try:
            for line in setup_file.read_text(encoding="utf-8").splitlines():
                if "version=" in line:
                    version = line.split("=")[1].strip(" '\",")
                    break
        except Exception:
            pass

    git_hash = None
    is_dirty = False
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short=10", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            git_hash = res.stdout.strip()
            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            if status_res.returncode == 0 and status_res.stdout.strip():
                is_dirty = True
    except Exception:
        pass

    # Source fingerprint from Python source files, assets, and spec
    h = hashlib.sha256()
    h.update(version.encode())
    for p in sorted(ROOT_DIR.rglob("*.py")):
        if any(part in p.parts for part in [".git", "__pycache__", "build", "dist", ".venv"]):
            continue
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        except Exception:
            pass

    for asset_name in ("Favicon.ico", "icon.png", "ollambo.spec", "requirements.txt"):
        asset_path = ROOT_DIR / asset_name
        if asset_path.exists():
            try:
                st = asset_path.stat()
                h.update(f"{asset_name}:{st.st_size}:{int(st.st_mtime)}".encode())
            except Exception:
                pass

    source_fingerprint = h.hexdigest()[:12]
    git_tag = f"-{git_hash}" if git_hash else ""
    dirty_tag = "-dirty" if is_dirty else ""
    build_id = f"v{version}{git_tag}{dirty_tag}-{source_fingerprint}"

    return {
        "version": version,
        "git_commit": git_hash,
        "is_dirty": is_dirty,
        "source_fingerprint": source_fingerprint,
        "build_id": build_id,
    }


def write_installation_manifest(install_dir: Path, identifier: dict, onefile: bool) -> None:
    """Save an installation manifest with build identifier so future runs can check for changes."""
    import datetime

    manifest_data = {
        "app": "Ollambo",
        "version": identifier.get("version", "1.0.0"),
        "git_commit": identifier.get("git_commit"),
        "is_dirty": identifier.get("is_dirty", False),
        "source_fingerprint": identifier.get("source_fingerprint"),
        "build_id": identifier.get("build_id"),
        "onefile": onefile,
        "installed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    manifest_path = install_dir / MANIFEST_FILENAME
    try:
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Note: Could not write installation manifest: {e}")


def check_existing_installation(install_dir: Path, current_identifier: dict | None = None) -> dict | None:
    """Check if Ollambo is already installed in the specified directory and inspect its manifest."""
    if not install_dir.exists():
        return None

    exe_name = "Ollambo.exe" if sys.platform == "win32" else "Ollambo"
    target_exe = install_dir / exe_name

    has_exe = target_exe.is_file()
    has_internal = (install_dir / "_internal").is_dir()
    has_icons = (install_dir / "Favicon.ico").is_file() or (install_dir / "icon.png").is_file()
    manifest_file = install_dir / MANIFEST_FILENAME
    has_manifest = manifest_file.is_file()

    manifest_data = None
    if has_manifest:
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if has_exe or has_internal or has_manifest or (has_icons and any(install_dir.glob("*.exe" if sys.platform == "win32" else "*"))):
        info = {
            "path": install_dir,
            "exe": target_exe if has_exe else None,
            "has_exe": has_exe,
            "modified_time": None,
            "manifest": manifest_data,
            "is_up_to_date": False,
        }
        if has_exe:
            try:
                import datetime
                stat = target_exe.stat()
                info["modified_time"] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        if current_identifier and manifest_data:
            installed_build_id = manifest_data.get("build_id")
            if installed_build_id and installed_build_id == current_identifier.get("build_id"):
                info["is_up_to_date"] = True

        return info

    # Non-empty directory without Ollambo signature
    try:
        if any(install_dir.iterdir()):
            return {
                "path": install_dir,
                "exe": None,
                "has_exe": False,
                "modified_time": None,
                "manifest": None,
                "is_up_to_date": False,
                "generic_non_empty": True,
            }
    except Exception:
        pass

    return None


def handle_existing_installation_prompt(
    install_dir: Path,
    current_identifier: dict,
    auto_update: bool = False,
    auto_yes: bool = False,
    force_override: bool = False,
) -> tuple[Path, bool]:
    """
    Check if target directory has an existing installation and prompt user if needed.
    Detects if the existing app is already up to date, giving user option to override.
    Returns (confirmed_install_dir, is_update).
    """
    while True:
        info = check_existing_installation(install_dir, current_identifier=current_identifier)
        if not info:
            return install_dir, False

        # --- SCENARIO 1: EXISTING APP IS ALREADY UP TO DATE ---
        if info.get("is_up_to_date"):
            if force_override:
                print_step(f"Existing installation at {install_dir} is up to date.")
                print_success("Override enabled (--override / --force). Proceeding with reinstallation.")
                return install_dir, True

            print("\n" + "=" * 55)
            print("     ✨  Ollambo is Already Up to Date!         ")
            print("=" * 55)
            print(f"Directory:       \033[1;32m{install_dir}\033[0m")
            installed_ver = info["manifest"].get("version", "1.0.0") if info.get("manifest") else "1.0.0"
            installed_build = info["manifest"].get("build_id", "Unknown") if info.get("manifest") else "Unknown"
            print(f"Installed Build: {installed_ver} ({installed_build})")
            print(f"Current Source:  {current_identifier['build_id']}")
            print("\n\033[1;36mNo changes detected. The installed application is completely up to date.\033[0m")

            print("\nOptions:")
            print("  • \033[1m[Enter / Q]\033[0m Exit (no action needed) [Default]")
            print("  • \033[1m[O]\033[0m Override / Reinstall anyway")
            print("  • \033[1m[C]\033[0m Choose a different folder\n")

            try:
                choice = input("Choice [Enter/Q=Exit, O=Override, C=Change folder] (default: Exit): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting.")
                sys.exit(0)

            if not choice or choice in ("q", "exit", "n"):
                print_success("Ollambo is already up to date. No changes were made.")
                sys.exit(0)
            elif choice in ("o", "override", "y"):
                print_success(f"Override confirmed: Reinstalling Ollambo in {install_dir}")
                return install_dir, True
            elif choice == "c":
                print("Select a new installation directory:")
                default_dir = get_default_install_dir()
                install_dir = prompt_installation_directory(default_dir)
                continue
            else:
                print("Invalid choice. Please enter Enter, O, or C.")
                continue

        # --- SCENARIO 2: CHANGES DETECTED OR OLDER INSTALLATION WITHOUT MANIFEST ---
        if auto_update or auto_yes or force_override:
            print_step(f"Existing installation detected at: {install_dir}")
            print_success("Proceeding with clean update.")
            return install_dir, True

        # Interactive update prompt
        print("\n" + "=" * 55)
        print("   ⚠️  Existing Ollambo Installation (Updates Available)  ")
        print("=" * 55)
        print(f"Directory: \033[1;33m{install_dir}\033[0m")

        if info.get("manifest"):
            print(f"Installed Build: {info['manifest'].get('build_id', 'Unknown')}")
            print(f"Target Build:    {current_identifier['build_id']}")
            print("\033[1;32mChanges detected: New update is ready to install.\033[0m")
        elif info.get("has_exe") and info.get("exe"):
            print(f"Found executable: {info['exe'].name}")
            if info.get("modified_time"):
                print(f"Installed/Modified: {info['modified_time']}")
            print(f"Target Build:     {current_identifier['build_id']}")
            print("\033[1;32mPrevious release detected. Ready to update.\033[0m")
        elif info.get("generic_non_empty"):
            print("\033[1;33mNote: Target directory already exists and contains files.\033[0m")

        print("\nOptions:")
        print("  • \033[1m[U]\033[0m Update / Reinstall (safely replaces application files) [Default]")
        print("  • \033[1m[C]\033[0m Choose a different folder")
        print("  • \033[1m[Q]\033[0m Cancel installation\n")

        try:
            choice = input("Choice [U=Update, C=Change folder, Q=Cancel] (default: U): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nInstallation cancelled.")
            sys.exit(0)

        if not choice or choice in ("u", "y"):
            print_success(f"Confirmed: Updating Ollambo in {install_dir}")
            return install_dir, True
        elif choice == "c":
            print("Select a new installation directory:")
            default_dir = get_default_install_dir()
            install_dir = prompt_installation_directory(default_dir)
            continue
        elif choice == "q":
            print("Installation cancelled. No changes were made.")
            sys.exit(0)
        else:
            print("Invalid choice. Please enter U, C, or Q.")


def clean_existing_app_files(install_dir: Path) -> None:
    """Safely remove previous application binaries and libraries prior to updating."""
    if not install_dir.exists():
        return

    print_step(f"Cleaning previous application binaries from: {install_dir}...")
    for item in install_dir.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except Exception as e:
            print_error(f"Failed to remove existing file '{item.name}': {e}")
            print("Please ensure Ollambo is not running and you have sufficient permissions.")
            raise
    print_success("Previous application files cleanly removed for update.")


def build_and_install(
    install_dir: Path,
    identifier: dict,
    onefile: bool = False,
    is_update: bool = False,
    auto_yes: bool = False,
) -> Path:
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

    # Make sure Ollambo isn't running before copying files
    ensure_ollambo_not_running(allow_prompt=not auto_yes)

    # If updating an existing directory, clean previous binaries to prevent DLL mismatch/corruption
    if is_update or install_dir.exists():
        clean_existing_app_files(install_dir)

    action_name = "Updating" if is_update else "Installing"
    print_step(f"{action_name} Ollambo into: {install_dir}...")
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

    # Save manifest with build identifier for update tracking
    write_installation_manifest(install_dir, identifier, onefile)

    # CLEAN UP WORKING DIRECTORY: remove temporary build/ and dist/
    print_step("Cleaning up temporary build artifacts from working directory...")
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)
    print_success("Working directory cleaned. No extra build files left behind.")

    if is_update:
        print_success(f"Ollambo updated successfully at:\n    {final_exe}")
    else:
        print_success(f"Ollambo installed successfully to:\n    {final_exe}")

    return final_exe


def install_dev_mode() -> None:
    print_step("Installing Ollambo in developer editable mode...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(ROOT_DIR)])
    print_success("Ollambo installed in editable mode! You can now run `ollambo` or `python main.py` anywhere.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install, update, and package Ollambo standalone desktop application.")
    parser.add_argument("--dir", type=str, default="", help="Custom installation directory path.")
    parser.add_argument("--update", "-u", action="store_true", help="Update existing installation if changes found without prompt.")
    parser.add_argument("--override", "--force", action="store_true", help="Override and reinstall even if existing installation is already up to date.")
    parser.add_argument("--yes", "-y", action="store_true", help="Automatically accept confirmation prompts.")
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

    # Compute current build identifier
    current_identifier = get_source_identifier()

    # Determine installation directory
    if args.dir:
        install_dir = Path(args.dir).expanduser().resolve()
        if install_dir.name.lower() != "ollambo" and not install_dir.suffix:
            install_dir = install_dir / "Ollambo"
    else:
        default_dir = get_default_install_dir()
        install_dir = prompt_installation_directory(default_dir)

    # Check for existing installation and prompt user if detected
    install_dir, is_update = handle_existing_installation_prompt(
        install_dir,
        current_identifier=current_identifier,
        auto_update=args.update,
        auto_yes=args.yes,
        force_override=args.override,
    )

    action_label = "Update" if is_update else "Installation"
    print(f"\nTarget {action_label.lower()} location: \033[1;32m{install_dir}\033[0m")

    # Ensure no running Ollambo instance locks files before building
    ensure_ollambo_not_running(allow_prompt=not args.yes)

    install_dependencies(include_dev=False)
    final_exe = build_and_install(
        install_dir=install_dir,
        identifier=current_identifier,
        onefile=args.onefile,
        is_update=is_update,
        auto_yes=args.yes,
    )

    if not args.no_shortcut and sys.platform == "win32":
        create_shortcuts(final_exe)

    status_title = "Update Complete!" if is_update else "Installation Complete!"
    print("\n" + "=" * 55)
    print(f"                  🎉 {status_title}                         ")
    print("=" * 55)
    print(f"Ollambo location:")
    print(f"  {final_exe}")
    print("\nTo launch Ollambo:")
    print("  • Double-click the Desktop shortcut 'Ollambo'")
    if sys.platform == "win32":
        print("  • Or find 'Ollambo' in the Windows Start Menu")
    print(f"  • Or run directly: {final_exe}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()


