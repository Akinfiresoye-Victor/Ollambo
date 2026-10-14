"""
workers/web_answer_worker.py - Fast web-augmented answer worker for chat mode.

When a chat prompt contains a URL or domain, this worker:
1. Fetches the page(s) directly with fetch_pages
2. Extracts plain text via extract_text
3. Ranks relevant passages with chunk_and_rank
4. Injects context using build_research_prompt
5. Streams the answer through OllamaClient
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient
from core.storage import get_configured_threads, get_effective_keep_alive
from core.web_research import (
    build_research_prompt,
    chunk_and_rank,
    extract_text,
    fetch_pages,
)


class WebAnswerWorker(QThread):
    """Fetches web page content, builds research prompt, and streams answer."""

    token_received = Signal(str)
    status_message = Signal(str)
    chat_finished = Signal(str, dict)
    chat_error = Signal(str)

    def __init__(
        self,
        ollama_url: str,
        model: str,
        prompt: str,
        urls: list[str],
        options: dict[str, Any] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._urls = urls
        self._options = options or {}
        self._stop = False

    def stop(self) -> None:
        """Cancel execution immediately."""
        self._stop = True

    def run(self) -> None:
        client = OllamaClient(self._url)
        try:
            domain = urlparse(self._urls[0]).netloc or self._urls[0]
            self.status_message.emit(f"Fetching {domain}")

            pages_raw = fetch_pages(self._urls)
            if self._stop:
                return

            self.status_message.emit("Reading page")
            pages: list[dict] = []
            for p in pages_raw:
                if self._stop:
                    return
                html = p.get("html", "")
                text = extract_text(html, url=p.get("url", ""))
                if text:
                    pages.append({
                        "url": p.get("url", ""),
                        "title": urlparse(p.get("url", "")).netloc,
                        "text": text,
                    })

            top_chunks = chunk_and_rank(self._prompt, pages, top_k=5) if pages else []
            full_prompt = build_research_prompt(self._prompt, top_chunks)

            if self._stop:
                return

            self.status_message.emit("Answering")

            chat_opts = dict(self._options)
            threads = chat_opts.get("num_thread") if "num_thread" in chat_opts else get_configured_threads(self._options)
            if threads is not None:
                chat_opts["num_thread"] = threads
            elif "num_thread" in chat_opts:
                chat_opts.pop("num_thread", None)

            if "temperature" not in chat_opts:
                chat_opts["temperature"] = 0.2
            if "top_p" not in chat_opts:
                chat_opts["top_p"] = 0.9

            keep_alive = get_effective_keep_alive(self._options)

            stream = client.chat_stream(
                model=self._model,
                messages=[{"role": "user", "content": full_prompt}],
                options=chat_opts,
                stop_flag=lambda: self._stop,
                keep_alive=keep_alive,
            )

            full_text: list[str] = []
            final_meta: dict[str, Any] = {}
            for chunk in stream:
                if self._stop:
                    break
                content = chunk.get("message", {}).get("content", "")
                if content:
                    full_text.append(content)
                    self.token_received.emit(content)
                if chunk.get("done", False):
                    final_meta = {
                        "done_reason": chunk.get("done_reason", "stop"),
                        "prompt_eval_count": chunk.get("prompt_eval_count", 0),
                        "eval_count": chunk.get("eval_count", 0),
                        "total_duration": chunk.get("total_duration", 0),
                    }
                    break

            if self._stop:
                self.chat_finished.emit(
                    "".join(full_text),
                    {"done_reason": "cancelled", "eval_count": len(full_text)},
                )
            else:
                self.chat_finished.emit("".join(full_text), final_meta)

        except Exception as e:
            self.chat_error.emit(f"Web answer failed: {e}")
        finally:
            client.close()
