"""
OS-specific Ollama installation helpers.

Design decisions from user feedback:
  - Linux: ALWAYS show manual steps (the official script needs sudo, and a
    background process can't type a password).
  - Windows: try winget first; if winget is missing, show manual steps.
  - macOS: try brew; if brew is missing, show manual steps.
  - On Windows, hide the subprocess console window (CREATE_NO_WINDOW).
  - Never run anything without the user confirming first.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable


@dataclass
class InstallPlan:
    """What the installer intends to do."""
    can_auto_install: bool
    command: list[str]           # The exact command to run (empty if manual-only).
    display_command: str         # Human-readable version shown to user before confirm.
    manual_steps: str            # Shown when auto-install isn't possible or fails.


def get_install_plan() -> InstallPlan:
    """Figure out how to install Ollama on this OS."""
    if sys.platform == "win32":
        return _windows_plan()
    elif sys.platform == "darwin":
        return _macos_plan()
    else:
        return _linux_plan()


# ── Per-OS plans ──────────────────────────────────────────────────────────

def _windows_plan() -> InstallPlan:
    manual = (
        "Manual install steps for Windows:\n"
        "1. Open https://ollama.com/download in your browser.\n"
        "2. Download the Windows installer.\n"
        "3. Run the installer and follow the prompts.\n"
        "4. After install, click 'Re-check' below."
    )
    if shutil.which("winget"):
        cmd = ["winget", "install", "Ollama.Ollama", "--accept-source-agreements"]
        return InstallPlan(
            can_auto_install=True,
            command=cmd,
            display_command="winget install Ollama.Ollama",
            manual_steps=manual,
        )
    return InstallPlan(
        can_auto_install=False,
        command=[],
        display_command="",
        manual_steps=(
            "winget is not available on this machine.\n\n" + manual
        ),
    )


def _macos_plan() -> InstallPlan:
    manual = (
        "Manual install steps for macOS:\n"
        "1. Open https://ollama.com/download in your browser.\n"
        "2. Download the macOS installer.\n"
        "3. Open the downloaded .dmg and drag Ollama to Applications.\n"
        "4. After install, click 'Re-check' below."
    )
    if shutil.which("brew"):
        cmd = ["brew", "install", "ollama"]
        return InstallPlan(
            can_auto_install=True,
            command=cmd,
            display_command="brew install ollama",
            manual_steps=manual,
        )
    return InstallPlan(
        can_auto_install=False,
        command=[],
        display_command="",
        manual_steps=(
            "Homebrew is not installed.\n\n" + manual
        ),
    )


def _linux_plan() -> InstallPlan:
    # Always manual on Linux — the official script needs sudo.
    return InstallPlan(
        can_auto_install=False,
        command=[],
        display_command="",
        manual_steps=(
            "Install steps for Linux:\n"
            "1. Open a terminal.\n"
            "2. Run: curl -fsSL https://ollama.ai/install.sh | sh\n"
            "   (This needs sudo — the installer will ask for your password.)\n"
            "3. After install, click 'Re-check' below."
        ),
    )


# ── Run the install command ───────────────────────────────────────────────

def _subprocess_kwargs() -> dict:
    """Extra kwargs to prevent a console window flashing on Windows."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def run_install(
    plan: InstallPlan,
    on_output: Callable[[str], None] | None = None,
    stop_flag: Callable[[], bool] | None = None,
) -> bool:
    """Execute the install command, streaming output line by line.

    Returns True on success, False on failure.
    *on_output* is called with each line of stdout/stderr.
    *stop_flag* is checked between lines to support cancellation.
    """
    if not plan.can_auto_install or not plan.command:
        return False

    try:
        proc = subprocess.Popen(
            plan.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line-buffered.
            **_subprocess_kwargs(),
        )
    except FileNotFoundError:
        if on_output:
            on_output(f"Command not found: {plan.command[0]}\n")
        return False
    except OSError as e:
        if on_output:
            on_output(f"Failed to start install: {e}\n")
        return False

    assert proc.stdout is not None
    for line in proc.stdout:
        if stop_flag and stop_flag():
            proc.kill()
            proc.wait()
            if on_output:
                on_output("\n[Cancelled by user]\n")
            return False
        if on_output:
            on_output(line)

    proc.wait()
    return proc.returncode == 0


# ── Start the Ollama server ──────────────────────────────────────────────

def start_ollama_server() -> subprocess.Popen | None:
    """Start 'ollama serve' in the background. Returns the Popen or None.

    The caller should track whether it started the server so it can
    stop it cleanly on exit (see main_window.py).
    """
    ollama_path = shutil.which("ollama")
    if not ollama_path:
        return None
    try:
        proc = subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_subprocess_kwargs(),
        )
        return proc
    except OSError:
        return None
