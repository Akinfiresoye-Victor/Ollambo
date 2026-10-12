"""
Background worker that runs the three setup checks without freezing the UI.

Emits signals for each check result so the wizard can update status lines
in real time. Also tries to start the server if Ollama is installed but
the server is down.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from core.installer import start_ollama_server
from core.setup_checker import (
    SetupStatus,
    get_installed_models,
    is_ollama_installed,
    is_server_running,
)


class SetupWorker(QThread):
    """Runs setup checks on a background thread."""

    # Signal args: (check_name: str, ok: bool, detail: str)
    check_result = Signal(str, bool, str)

    # Emitted when all checks are done.
    #   (all_ok: bool, installed_models: list[str], server_proc_started: bool)
    all_done = Signal(bool, list, bool)

    def __init__(self, ollama_url: str, parent=None) -> None:
        super().__init__(parent)
        self._ollama_url = ollama_url
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        all_ok = True
        models: list[str] = []
        started_server = False

        # ── Check 1: Ollama installed? ────────────────────────────────
        status = is_ollama_installed()
        self.check_result.emit("installed", status.ok, status.detail)
        if not status.ok:
            self.all_done.emit(False, [], False)
            return
        if self._stop:
            return

        # ── Check 2: Server running? ──────────────────────────────────
        status = is_server_running(self._ollama_url)
        if not status.ok:
            # Try to start the server automatically.
            self.check_result.emit("server", False, "Not running — starting it…")
            proc = start_ollama_server()
            if proc:
                started_server = True
                # Wait up to 10 seconds for the server to come up.
                for _ in range(20):
                    if self._stop:
                        return
                    time.sleep(0.5)
                    status = is_server_running(self._ollama_url)
                    if status.ok:
                        break

        self.check_result.emit("server", status.ok, status.detail)
        if not status.ok:
            all_ok = False
            self.all_done.emit(False, [], started_server)
            return
        if self._stop:
            return

        # ── Check 3: Models available? ────────────────────────────────
        status, models = get_installed_models(self._ollama_url)
        self.check_result.emit("models", status.ok, status.detail)
        if not status.ok:
            all_ok = False

        self.all_done.emit(all_ok, models, started_server)
