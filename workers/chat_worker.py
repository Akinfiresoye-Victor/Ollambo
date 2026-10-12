"""
Background worker for streaming chat completions from Ollama.

Runs compaction (if conversation exceeds 70% of num_ctx) on the background
thread BEFORE sending the chat request.
Emits tokens as they arrive, and emits chat_finished when complete.
Checks a stop flag on every token to support immediate cancellation.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from core.context_manager import (
    build_compacted_messages,
    build_extractive_summary,
    estimate_messages_tokens,
    fallback_truncate_messages,
    generate_summary,
    is_compaction_needed,
    split_for_compaction,
)
from core.ollama_client import OllamaClient


class ChatWorker(QThread):
    """Streams Ollama chat responses on a background thread with auto-compaction."""

    # Emitted for each incoming token chunk.
    token_received = Signal(str)

    # Emitted when compaction begins: (current_tokens, threshold_tokens)
    compaction_started = Signal(int, int)

    # Emitted when compaction runs: (compacted_messages, old_token_count, new_token_count)
    compaction_performed = Signal(list, int, int)

    # Emitted when the model finishes response.
    # Passes: (full_content: str, metadata: dict)
    chat_finished = Signal(str, dict)

    # Emitted on connection or model error.
    chat_error = Signal(str)

    def __init__(
        self,
        ollama_url: str,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        summarizer_model: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._messages = messages
        self._options = options or {}
        self._summarizer_model = summarizer_model or model
        self._stop = False

    def stop(self) -> None:
        """Request immediate thread cancellation."""
        self._stop = True

    def run(self) -> None:
        num_ctx = int(self._options.get("num_ctx", 8192))
        est_tokens = estimate_messages_tokens(self._messages)

        # ── 1. Pre-chat Compaction Check ──────────────────────────────
        payload_messages = self._messages
        threshold_limit = int(num_ctx * 0.75)
        if is_compaction_needed(est_tokens, num_ctx, threshold_ratio=0.75):
            self.compaction_started.emit(est_tokens, threshold_limit)
            sys_msgs, to_summarize, recent_msgs = split_for_compaction(self._messages, keep_recent=4)
            if to_summarize:
                try:
                    summary = generate_summary(
                        ollama_url=self._url,
                        model=self._summarizer_model,
                        messages_to_summarize=to_summarize,
                        stop_flag=lambda: self._stop,
                    )
                    if summary and not self._stop:
                        payload_messages = build_compacted_messages(sys_msgs, summary, recent_msgs)
                    else:
                        summary = build_extractive_summary(to_summarize)
                        payload_messages = build_compacted_messages(sys_msgs, summary, recent_msgs)
                except Exception:
                    summary = build_extractive_summary(to_summarize)
                    payload_messages = build_compacted_messages(sys_msgs, summary, recent_msgs)

                new_tokens = estimate_messages_tokens(payload_messages)
                self.compaction_performed.emit(payload_messages, est_tokens, new_tokens)

        if self._stop:
            return

        # ── 2. Stream Chat Response ───────────────────────────────────
        client = OllamaClient(self._url)
        full_text: list[str] = []
        final_meta: dict[str, Any] = {}

        import os
        optimal_threads = min(8, max(2, os.cpu_count() or 4))
        chat_opts = dict(self._options)
        if "num_thread" not in chat_opts:
            chat_opts["num_thread"] = optimal_threads
        if "temperature" not in chat_opts:
            chat_opts["temperature"] = 0.2
        if "top_p" not in chat_opts:
            chat_opts["top_p"] = 0.9

        try:
            stream = client.chat_stream(
                model=self._model,
                messages=payload_messages,
                options=chat_opts,
                stop_flag=lambda: self._stop,
            )

            for chunk in stream:
                if self._stop:
                    break

                msg = chunk.get("message", {})
                content = msg.get("content", "")
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
            self.chat_error.emit(f"Chat request failed: {e}")
        finally:
            client.close()
