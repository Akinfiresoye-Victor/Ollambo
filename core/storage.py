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
    "max_agents": 1,              # Default 1 worker agent for lower laptop load
    "auto_test": True,            # Automatically test and self-heal generated code
    "setup_done": False,          # True after first successful setup.
    "low_power_mode": False,      # Low-Power Mode for reduced CPU, RAM, and VRAM
    "use_model_summaries": False, # False = fast extractive compaction (zero LLM calls)
    "keep_alive": "3m",           # Time to keep model warm in memory
    "num_thread": None,           # None = let Ollama choose optimal threads
    "internet_access": False,     # OFF by default — web research is opt-in only
    "auto_approve_research_plans": False, # Auto-approve deep research plans
}



def get_system_ram_gb() -> float:
    """Return total physical RAM in gigabytes using psutil if available, otherwise OS fallback."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        pass
    try:
        import sys
        if sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        return float(parts[1]) / (1024 * 1024)
        elif sys.platform == "darwin":
            import subprocess
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()
            return float(out) / (1024 ** 3)
    except Exception:
        pass
    return 8.0


def get_available_ram_gb() -> float:
    """Return available physical RAM in gigabytes using psutil if available, otherwise OS fallback."""
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        pass
    try:
        import sys
        if sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullAvailPhys / (1024 ** 3)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        return float(parts[1]) / (1024 * 1024)
    except Exception:
        pass
    return get_system_ram_gb() * 0.5


def calculate_auto_num_ctx(
    available_ram_gb: float,
    model_max_ctx: int = 8192,
    low_power: bool = False,
) -> int:
    """Calculate optimal num_ctx based on available RAM and model limit.

    Never exceeds model_max_ctx.
    """
    if low_power:
        recommended = 2048 if available_ram_gb < 6 else 4096
    elif available_ram_gb >= 16:
        recommended = 16384
    elif available_ram_gb >= 8:
        recommended = 8192
    elif available_ram_gb >= 4:
        recommended = 4096
    else:
        recommended = 2048

    if model_max_ctx and model_max_ctx > 0:
        return max(1024, min(recommended, model_max_ctx))
    return max(1024, recommended)


def resolve_num_ctx(
    settings: dict[str, Any] | None = None,
    model_max_ctx: int = 8192,
    low_power: bool | None = None,
) -> int:
    """Resolve effective num_ctx: respects user setting, clamped never above model's real limit."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = {}

    is_lp = settings.get("low_power_mode", False) if low_power is None else low_power

    # If low_power is active and user has not customized num_ctx, use low_power auto
    user_val = settings.get("num_ctx")
    avail_ram = get_available_ram_gb()

    if user_val is not None:
        try:
            val_int = int(user_val)
            if val_int > 0:
                if is_lp and val_int > 4096:
                    val_int = 4096
                if model_max_ctx and model_max_ctx > 0:
                    return max(1024, min(val_int, model_max_ctx))
                return max(1024, val_int)
        except (ValueError, TypeError):
            pass

    return calculate_auto_num_ctx(avail_ram, model_max_ctx, low_power=is_lp)


def get_configured_threads(settings: dict[str, Any] | None = None) -> int | None:
    """Return user-configured num_thread from settings, or None to let Ollama choose."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = {}
    val = settings.get("num_thread")
    if val is not None:
        try:
            val_int = int(val)
            if val_int > 0:
                return val_int
        except (ValueError, TypeError):
            pass
    return None


def parse_keep_alive_seconds(val: str | int | float) -> int:
    """Parse keep_alive string like '3m', '1m', '90s', or integer into seconds."""
    if isinstance(val, (int, float)):
        return max(10, int(val))
    val_str = str(val).strip().lower()
    if val_str.endswith("m"):
        try:
            return max(10, int(float(val_str[:-1]) * 60))
        except ValueError:
            return 180
    if val_str.endswith("s"):
        try:
            return max(10, int(float(val_str[:-1])))
        except ValueError:
            return 180
    try:
        return max(10, int(val_str))
    except ValueError:
        return 180


def get_effective_keep_alive(settings: dict[str, Any] | None = None) -> str:
    """Return effective keep_alive duration string (e.g. '1m' when low_power_mode is on)."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = {}
    if settings.get("low_power_mode", False):
        return "1m"
    return str(settings.get("keep_alive", "3m")).strip() or "3m"


def get_effective_keep_alive_seconds(settings: dict[str, Any] | None = None) -> int:
    """Return effective keep_alive in seconds (60 when low_power_mode is on)."""
    return parse_keep_alive_seconds(get_effective_keep_alive(settings))


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

