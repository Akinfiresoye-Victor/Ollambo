"""
Background worker that queries Ollama for installed models without freezing the UI.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient


class ModelsWorker(QThread):
    """Fetches model list from Ollama on a background thread."""

    models_ready = Signal(list)
    models_error = Signal(str)

    def __init__(self, ollama_url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url

    def run(self) -> None:
        try:
            client = OllamaClient(self._url)
            models = client.list_models()
            self.models_ready.emit(models)
        except Exception as e:
            self.models_error.emit(str(e))
