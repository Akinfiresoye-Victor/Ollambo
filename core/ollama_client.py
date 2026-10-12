"""
Thin wrapper around the Ollama REST API.

Every method is a plain blocking call (httpx, no async).
Workers call these methods from background threads so the UI stays responsive.

Uses a persistent httpx.Client for connection pooling — eliminates TCP
handshake overhead on consecutive requests, improving throughput substantially.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

import httpx

# Default timeout: 10 s for connection, 5 min for read (model inference is slow).
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


class OllamaClient:
    """Talks to one Ollama server via a persistent HTTP/1.1 connection pool."""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url.rstrip("/")
        # Persistent connection-pooled client — reuses TCP connections across
        # successive chat_stream / generate / list_models calls.
        self._session = httpx.Client(
            base_url=self.base_url,
            timeout=DEFAULT_TIMEOUT,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=120,  # seconds
            ),
        )

    def close(self) -> None:
        """Close the persistent HTTP session."""
        self._session.close()

    # ── Health ─────────────────────────────────────────────────────────

    def is_server_running(self) -> bool:
        """Return True if the Ollama server responds."""
        try:
            r = self._session.get("/api/tags", timeout=httpx.Timeout(5.0))
            return r.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            return False

    # ── Models ─────────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return names of all locally-installed models."""
        r = self._session.get("/api/tags")
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]

    def show_model(self, model: str) -> dict[str, Any]:
        """Return model details (parameters, template, context length, etc.)."""
        r = self._session.post("/api/show", json={"name": model})
        r.raise_for_status()
        return r.json()

    def get_context_length(self, model: str) -> int:
        """Read the model's maximum context length from its parameters.

        Falls back to 4096 if the info isn't available.
        """
        try:
            info = self.show_model(model)
            # Ollama exposes model params as a multi-line string in 'modelfile'
            # or as structured data in newer versions.
            params = info.get("model_info", {})
            # Try the structured path first (Ollama ≥ 0.3):
            for key, value in params.items():
                if "context_length" in key:
                    return int(value)
            # Fallback: parse the modelfile text for num_ctx.
            modelfile = info.get("modelfile", "")
            for line in modelfile.splitlines():
                stripped = line.strip().lower()
                if stripped.startswith("parameter num_ctx"):
                    parts = stripped.split()
                    if len(parts) >= 3:
                        return int(parts[2])
        except Exception:
            pass
        return 4096

    # ── Pull (download a model) ────────────────────────────────────────

    def pull_model(
        self,
        model: str,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        stop_flag: Callable[[], bool] | None = None,
    ) -> None:
        """Download *model*, calling on_progress(chunk) for each status line.

        Each chunk is a dict like:
          {"status": "downloading ...", "total": 12345, "completed": 6789}
        """
        with self._session.stream(
            "POST",
            "/api/pull",
            json={"name": model, "stream": True},
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if stop_flag and stop_flag():
                    return
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_progress:
                    on_progress(chunk)

    # ── Chat (streaming) ───────────────────────────────────────────────

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        format_schema: dict | str | None = None,
        stop_flag: Callable[[], bool] | None = None,
        keep_alive: str = "3m",
    ) -> Iterator[dict[str, Any]]:
        """Yield dicts from a streaming /api/chat call.

        *keep_alive* keeps the model loaded in Ollama's memory for the given duration
        (e.g. '3m' = 3 minutes). Prevents slow reloading on consecutive prompts.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
        }
        if options:
            body["options"] = options
        if format_schema is not None:
            body["format"] = format_schema

        with self._session.stream(
            "POST",
            "/api/chat",
            json=body,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if stop_flag and stop_flag():
                    return
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield chunk
                if chunk.get("done", False):
                    break

    def preload_model(self, model: str, keep_alive: str = "3m") -> bool:
        """Preload model into memory so subsequent requests respond instantly."""
        try:
            r = self._session.post(
                "/api/generate",
                json={"model": model, "keep_alive": keep_alive},
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
            return r.status_code == 200
        except Exception:
            return False

    def unload_model(self, model: str) -> bool:
        """Explicitly unload model from memory to free VRAM/RAM."""
        try:
            r = self._session.post(
                "/api/generate",
                json={"model": model, "keep_alive": 0},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            return r.status_code == 200
        except Exception:
            return False
