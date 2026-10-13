"""
Check whether the user's machine is ready to run Ollambo.

Three checks, run in order:
  1. Is Ollama installed?   (shutil.which + common install locations)
  2. Is the Ollama server running?  (GET /api/tags)
  3. Does the user have at least one model?

All functions are blocking — run them from a worker thread.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import NamedTuple

from core.ollama_client import OllamaClient

# Models we suggest to new users — small, fast, good at code.
SUGGESTED_MODELS: list[dict[str, str]] = [
    {"name": "qwen2.5-coder:1.5b",  "desc": "Qwen 2.5 Coder 1.5 B — tiny, fast, good at code"},
    {"name": "qwen2.5-coder:7b",    "desc": "Qwen 2.5 Coder 7 B — solid all-round coder"},
    {"name": "llama3.2:3b",          "desc": "Llama 3.2 3 B — small general-purpose model"},
    {"name": "deepseek-coder-v2:16b","desc": "DeepSeek Coder V2 16 B — powerful, needs more RAM"},
]


def check_hardware_capabilities() -> dict[str, Any]:
    """Check machine hardware (RAM) and recommend low-power settings if 8 GB or less."""
    from core.storage import get_system_ram_gb
    total_ram = get_system_ram_gb()
    is_low_ram = total_ram <= 8.5
    # Small quantized model (1.5B to 3B) from SUGGESTED_MODELS
    rec_model = "qwen2.5-coder:1.5b" if is_low_ram else "qwen2.5-coder:7b"
    return {
        "total_ram_gb": round(total_ram, 1),
        "is_low_ram": is_low_ram,
        "recommended_model": rec_model,
        "suggest_low_power": is_low_ram,
    }


class SetupStatus(NamedTuple):
    """Result of a single setup check."""
    ok: bool
    detail: str  # Short human-readable explanation.


def is_ollama_installed() -> SetupStatus:
    """Check if the 'ollama' binary exists on this machine."""
    # 1) shutil.which covers anything on PATH.
    path = shutil.which("ollama")
    if path:
        return SetupStatus(True, f"Found at {path}")

    # 2) Check common install locations per OS.
    extra_paths: list[Path] = []
    if sys.platform == "win32":
        local_app = Path.home() / "AppData" / "Local" / "Programs" / "Ollama"
        extra_paths.append(local_app / "ollama.exe")
        # Also check Program Files.
        for pf in [Path("C:/Program Files/Ollama"), Path("C:/Program Files (x86)/Ollama")]:
            extra_paths.append(pf / "ollama.exe")
    elif sys.platform == "darwin":
        extra_paths.append(Path("/usr/local/bin/ollama"))
        extra_paths.append(Path("/opt/homebrew/bin/ollama"))
    else:
        extra_paths.append(Path("/usr/local/bin/ollama"))
        extra_paths.append(Path("/usr/bin/ollama"))

    for p in extra_paths:
        if p.exists():
            return SetupStatus(True, f"Found at {p}")

    return SetupStatus(False, "Ollama is not installed")


def is_server_running(base_url: str = "http://localhost:11434") -> SetupStatus:
    """Check if the Ollama server is responding."""
    client = OllamaClient(base_url)
    if client.is_server_running():
        return SetupStatus(True, "Server is running")
    return SetupStatus(False, "Server is not responding")


def get_installed_models(
    base_url: str = "http://localhost:11434",
) -> tuple[SetupStatus, list[str]]:
    """Return (status, list_of_model_names)."""
    client = OllamaClient(base_url)
    try:
        models = client.list_models()
        if models:
            return SetupStatus(True, f"{len(models)} model(s) available"), models
        return SetupStatus(False, "No models installed"), []
    except Exception as e:
        return SetupStatus(False, f"Could not list models: {e}"), []
