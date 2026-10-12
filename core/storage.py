"""
Save and load settings and chats as JSON files.

Uses platformdirs so paths are correct on every OS:
  - Windows: %LOCALAPPDATA%/ollambo/
  - macOS:   ~/Library/Application Support/ollambo/
  - Linux:   ~/.local/share/ollambo/

All writes are ATOMIC (write to temp file, then rename) so a crash
mid-save cannot corrupt data.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

# ── Paths ──────────────────────────────────────────────────────────────────

APP_NAME = "ollambo"

def _data_dir() -> Path:
    """Return the app's data directory, creating it if needed."""
    path = Path(user_data_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chats_dir() -> Path:
    """Return the directory where chats are stored."""
    path = _data_dir() / "chats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_path() -> Path:
    return _data_dir() / "settings.json"


# ── Atomic write helper ───────────────────────────────────────────────────

def _atomic_write(path: Path, data: str) -> None:
    """Write *data* to *path* atomically.

    Strategy: write to a temp file in the same directory (so it's on the
    same filesystem), then os.replace() which is atomic on all major OSes.
    If anything fails, the temp file is cleaned up and the original file
    is untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file so we don't leak disk space.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Settings ──────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "ollama_url": "http://localhost:11434",
    "default_model": "",
    "summarizer_model": "",       # Empty = use the same model.
    "num_ctx": 8192,
    "coding_mode": "auto",        # 'auto' (AI decides), 'code', or 'chat'
    "max_continuations": 8,
    "max_agents": 2,              # Concurrent worker agents for code generation
    "auto_test": True,            # Automatically test and self-heal generated code
    "setup_done": False,          # True after first successful setup.
}


def load_settings() -> dict[str, Any]:
    """Load settings from disk, filling in defaults for missing keys."""
    path = _settings_path()
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            settings.update(saved)
        except (json.JSONDecodeError, OSError):
            # Corrupted file — fall back to defaults silently.
            pass
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to disk (atomic write)."""
    text = json.dumps(settings, indent=2, ensure_ascii=False)
    _atomic_write(_settings_path(), text)


# ── Chat persistence ─────────────────────────────────────────────────────
# Each chat is one JSON file: { "id": "...", "title": "...", "messages": [...] }
# Messages follow Ollama's format: {"role": "user"|"assistant"|"system", "content": "..."}

def list_chats() -> list[dict[str, str]]:
    """Return a list of {id, title} dicts for every saved chat, newest first.

    Automatically cleans up empty ghost chat files (no messages and no artifacts).
    """
    chats_dir = _chats_dir()
    result = []
    for p in sorted(chats_dir.glob("*.json"), key=os.path.getmtime, reverse=True):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Filter out and clean up ghost chats that contain no content
            if not data.get("messages") and not data.get("artifacts"):
                p.unlink(missing_ok=True)
                continue
            result.append({"id": p.stem, "title": data.get("title", "Untitled")})
        except (json.JSONDecodeError, OSError):
            continue
    return result


def load_chat(chat_id: str) -> dict[str, Any] | None:
    """Load a single chat by its ID. Returns None if not found."""
    path = _chats_dir() / f"{chat_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_chat(chat: dict[str, Any]) -> None:
    """Save a chat dict to disk (atomic). The chat must have an 'id' key.

    Never writes empty conversations to disk to prevent ghost chats.
    """
    if not chat.get("messages") and not chat.get("artifacts"):
        return
    chat_id = chat["id"]
    path = _chats_dir() / f"{chat_id}.json"
    text = json.dumps(chat, indent=2, ensure_ascii=False)
    _atomic_write(path, text)


def delete_chat(chat_id: str) -> None:
    """Delete a saved chat file."""
    path = _chats_dir() / f"{chat_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ── Work session persistence (build checkpoints) ─────────────────────────
# A work session stores the active build state so the user can resume
# after switching chats, crashes, or errors.

def _sessions_dir() -> Path:
    path = _data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_work_session(session: dict[str, Any]) -> None:
    """Save an active work session to disk (atomic)."""
    chat_id = session["chat_id"]
    path = _sessions_dir() / f"{chat_id}.json"
    _atomic_write(path, json.dumps(session, indent=2, ensure_ascii=False))


def load_work_session(chat_id: str) -> dict[str, Any] | None:
    """Load a work session for a given chat_id. Returns None if not found."""
    path = _sessions_dir() / f"{chat_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def delete_work_session(chat_id: str) -> None:
    """Remove a completed or cancelled work session."""
    path = _sessions_dir() / f"{chat_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def list_active_sessions() -> list[str]:
    """Return chat_ids that have an active (incomplete) work session."""
    result = []
    for p in _sessions_dir().glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data.get("completed", False):
                result.append(p.stem)
        except (json.JSONDecodeError, OSError):
            continue
    return result

