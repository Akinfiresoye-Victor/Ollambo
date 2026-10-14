"""
workers/title_worker.py

Background worker that asks Ollama to summarize what the user is discussing
and produce a concise conversation title without blocking the GUI.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.chat_title import generate_chat_title
from core.ollama_client import OllamaClient


class TitleWorker(QThread):
    """Summarizes user intent and emits a concise title for the chat."""

    title_ready = Signal(str, str)  # (chat_id, title)

    def __init__(
        self,
        chat_id: str,
        ollama_url: str,
        model: str,
        prompt: str | list[dict[str, str]],
        num_ctx: int = 2048,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chat_id = chat_id
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._num_ctx = num_ctx

    def run(self) -> None:
        if not self._model or not self._url:
            return

        client = OllamaClient(self._url)
        try:
            title = generate_chat_title(
                self._prompt,
                ollama_url=self._url,
                model=self._model,
                num_ctx=self._num_ctx,
                client=client,
            )
            if title:
                self.title_ready.emit(self._chat_id, title)
        except Exception:
            pass
        finally:
            client.close()
