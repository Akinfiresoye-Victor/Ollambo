"""
Background worker that pulls (downloads) an Ollama model.

Emits progress signals for the UI to show a progress bar.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient


class PullWorker(QThread):
    """Downloads a model on a background thread."""

    # (status_text: str, completed_bytes: int, total_bytes: int)
    progress = Signal(str, int, int)

    # (success: bool, message: str)
    finished_pull = Signal(bool, str)

    def __init__(self, model: str, ollama_url: str, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._ollama_url = ollama_url
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        client = OllamaClient(self._ollama_url)
        try:
            client.pull_model(
                self._model,
                on_progress=self._on_progress,
                stop_flag=lambda: self._stop,
            )
            if self._stop:
                self.finished_pull.emit(False, "Download cancelled.")
            else:
                self.finished_pull.emit(True, f"Model '{self._model}' is ready.")
        except Exception as e:
            self.finished_pull.emit(False, f"Pull failed: {e}")

    def _on_progress(self, chunk: dict[str, Any]) -> None:
        status = chunk.get("status", "")
        total = chunk.get("total", 0)
        completed = chunk.get("completed", 0)
        self.progress.emit(status, completed, total)
