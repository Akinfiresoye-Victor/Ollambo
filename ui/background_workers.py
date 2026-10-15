"""Small QThread helpers used by ChatPage and SessionController.

Kept out of chat_page.py so the controller can import them without a cycle.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient
from core.project_router import route_with_llm
from core.task_classifier import classify_task


class ContextLengthWorker(QThread):
    """Fetch model context length off the GUI thread to prevent freezing."""

    result_ready = Signal(int)

    def __init__(self, ollama_url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model

    def run(self) -> None:
        client = OllamaClient(self._url)
        ctx = client.get_context_length(self._model)
        client.close()
        self.result_ready.emit(ctx)


class ClassifyWorker(QThread):
    """Run LLM-based task classification off the GUI thread to prevent freezing."""

    result_ready = Signal(str)  # Emits 'chat', 'code', or 'both'

    def __init__(self, ollama_url: str, model: str, prompt: str, num_ctx: int = 8192, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._num_ctx = num_ctx

    def run(self) -> None:
        client = OllamaClient(self._url)
        try:
            result = classify_task(self._prompt, self._url, self._model, num_ctx=self._num_ctx, client=client)
            self.result_ready.emit(result)
        finally:
            client.close()


class RouteWorker(QThread):
    """Run LLM-based project routing off the GUI thread when heuristic is ambiguous."""

    result_ready = Signal(str)  # Emits 'continue' or 'new'

    def __init__(
        self,
        ollama_url: str,
        model: str,
        prompt: str,
        workspace_summary: dict | None,
        num_ctx: int = 8192,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._ws_summary = workspace_summary
        self._num_ctx = num_ctx

    def run(self) -> None:
        decision = route_with_llm(
            self._url,
            self._model,
            self._prompt,
            self._ws_summary,
            num_ctx=self._num_ctx,
        )
        self.result_ready.emit(decision)


class PreloadWorker(QThread):
    """Background thread to preload/warm a model into RAM/VRAM without freezing UI."""

    finished_preload = Signal(str, bool)

    def __init__(self, ollama_url: str, model: str, keep_alive: str = "3m", parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._keep_alive = keep_alive

    def run(self) -> None:
        client = OllamaClient(self._url)
        ok = client.preload_model(self._model, keep_alive=self._keep_alive)
        client.close()
        self.finished_preload.emit(self._model, ok)


class UnloadWorker(QThread):
    """Background thread to unload model when keep-alive window expires."""

    def __init__(self, ollama_url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model

    def run(self) -> None:
        client = OllamaClient(self._url)
        client.unload_model(self._model)
        client.close()
