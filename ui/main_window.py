"""
Main application window.

Uses a QStackedWidget to switch between:
  - Page 0: SetupWizard (shown on first launch or when checks fail)
  - Page 1: ChatPage   (shown after setup passes)

Tracks whether Ollambo started the Ollama server so it can stop it
on exit — but NEVER kills an Ollama instance the user was already running.
"""

from __future__ import annotations

import subprocess
from typing import Any

from PySide6.QtCore import QSettings, Slot
from PySide6.QtWidgets import (
    QMainWindow,
    QStackedWidget,
)

from core.ollama_client import OllamaClient
from core.storage import load_settings, save_settings
from ui.chat_page import ChatPage
from ui.setup_wizard import SetupWizard


class MainWindow(QMainWindow):
    """Top-level window that hosts the setup wizard and chat page."""

    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__()
        self._settings = settings
        self._server_proc: subprocess.Popen | None = None  # Set if WE started it.
        self._server_started_by_us = False

        self.setWindowTitle("Ollambo")
        self.setMinimumSize(1040, 680)
        self.resize(1200, 780)
        self._ui_settings = QSettings("Ollambo", "Ollambo")
        from core.assets import get_app_icon
        app_icon = get_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        # ── Pages ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        ollama_url = self._settings.get("ollama_url", "http://localhost:11434")

        setup_done = bool(self._settings.get("setup_done", False))
        self._wizard = SetupWizard(ollama_url, setup_done=setup_done, parent=self)
        self._wizard.setup_complete.connect(self._on_setup_complete)
        self._stack.addWidget(self._wizard)  # index 0

        self._chat_page = ChatPage(self._settings, parent=self)
        self._stack.addWidget(self._chat_page)  # index 1
        geometry = self._ui_settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter_state = self._ui_settings.value("window/splitter")
        if splitter_state is not None:
            self._chat_page.shell_splitter.restoreState(splitter_state)
        sidebar_collapsed = bool(
            self._ui_settings.value("window/sidebar_collapsed", False, type=bool)
        )
        self._chat_page.set_sidebar_collapsed(sidebar_collapsed)

        # ── Decide which page to show first ───────────────────────────
        if self._settings.get("setup_done"):
            # Still do a quick re-check in the background.
            self._wizard.run_checks()
            # But show the wizard while checking (it auto-continues if OK).
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(0)
            self._wizard.run_checks()

    # ── Slots ─────────────────────────────────────────────────────────

    @Slot(list, bool)
    def _on_setup_complete(self, models: list[str], server_started_by_us: bool) -> None:
        """Called when the setup wizard says everything is ready."""
        self._server_started_by_us = server_started_by_us

        # Persist that setup succeeded.
        self._settings["setup_done"] = True
        save_settings(self._settings)

        if models:
            self._chat_page.set_models(models)
        else:
            # Silently fetch in background without freezing
            from workers.models_worker import ModelsWorker
            url = self._settings.get("ollama_url", "http://localhost:11434")
            self._models_worker = ModelsWorker(url, parent=self)
            self._models_worker.models_ready.connect(self._chat_page.set_models)
            self._models_worker.start()

        self._stack.setCurrentIndex(1)

    # ── Clean exit ────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Stop workers and (if we started it) the Ollama server."""
        self._ui_settings.setValue("window/geometry", self.saveGeometry())
        self._ui_settings.setValue("window/splitter", self._chat_page.shell_splitter.saveState())
        self._ui_settings.setValue(
            "window/sidebar_collapsed", self._chat_page.sidebar_collapsed
        )
        # Stop any wizard or chat workers.
        self._wizard.stop_workers()
        self._chat_page.stop_workers()

        # Only stop the server if Ollambo started it.
        if self._server_started_by_us:
            self._stop_ollama_server()

        event.accept()

    def _stop_ollama_server(self) -> None:
        """Terminate the Ollama server process we started."""
        # Try 'ollama stop' first (graceful), then kill the process.
        import shutil, sys
        ollama = shutil.which("ollama")
        if ollama:
            try:
                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                subprocess.run(
                    [ollama, "stop"],
                    timeout=5,
                    capture_output=True,
                    **kwargs,
                )
            except Exception:
                pass
