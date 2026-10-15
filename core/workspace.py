"""
core/workspace.py - Real-folder workspace service for Ollambo project builds.

Each project lives in: <root>/<slug>/
  <relpath>                 - user code / generated files directly at relative paths
  .ollambo/project.json     - metadata (id, name, slug, kind, goal, created, updated, files index)
  .ollambo/backups/         - backups of overwritten files (<relpath>.<timestamp>.bak)

Central index: <root>/index.json
  Fast cache of all workspaces, rebuilt automatically if missing or corrupt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from core.context_manager import estimate_tokens
from core.pathsafe import safe_relpath

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".pyc", ".pyo", ".pyd",
    ".pdf", ".docx", ".xlsx",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".db", ".sqlite", ".sqlite3",
}


def _atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _workspace_root() -> Path:
    """Return the base directory for all workspaces."""
    from core.storage import _data_dir, load_settings
    try:
        settings = load_settings()
        custom = (settings.get("workspaces_dir") or "").strip()
        if custom:
            p = Path(custom)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    p = _data_dir() / "workspaces"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return _workspace_root() / "index.json"


def slugify(name: str) -> str:
    """Convert a name into an ASCII, lowercase, hyphenated slug (max 40 chars).

    Never returns a Windows reserved device name.
    """
    if not name:
        return "project"
    # Normalize unicode to ASCII
    clean = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    clean = clean.lower()
    # Replace non-alphanumeric chars with hyphens
    clean = re.sub(r"[^a-z0-9]+", "-", clean)
    clean = clean.strip("-")
    if not clean:
        clean = "project"
    clean = clean[:40].rstrip("-")
    if not clean:
        clean = "project"
    # Avoid Windows reserved device names
    base = clean.split("-")[0].upper()
    if clean.upper() in _WINDOWS_RESERVED or base in _WINDOWS_RESERVED:
        clean = f"p-{clean}"[:40].rstrip("-")
    return clean


def unique_slug(root: Path, base_slug: str) -> str:
    """Ensure base_slug is unique in root by appending -2, -3, etc."""
    slug = base_slug
    if not (root / slug).exists():
        return slug
    counter = 2
    while (root / f"{base_slug}-{counter}").exists():
        counter += 1
    return f"{base_slug}-{counter}"


def rebuild_index() -> list[dict[str, Any]]:
    """Scan workspace root folders and rebuild index.json from .ollambo/project.json."""
    root = _workspace_root()
    entries: list[dict[str, Any]] = []
    if root.exists():
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            pj = d / ".ollambo" / "project.json"
            if pj.is_file():
                try:
                    meta = json.loads(pj.read_text(encoding="utf-8"))
                    entries.append({
                        "id": meta.get("id", str(uuid.uuid4())),
                        "name": meta.get("name", d.name),
                        "slug": meta.get("slug", d.name),
                        "kind": meta.get("kind", "code"),
                        "goal": meta.get("goal", ""),
                        "created": meta.get("created", ""),
                        "updated": meta.get("updated", ""),
                        "chat_ids": meta.get("chat_ids", []),
                        "status": meta.get("status", "active"),
                        "summary": meta.get("summary", ""),
                        "file_count": len(meta.get("files", {})),
                    })
                except Exception:
                    continue
    # Sort newest first
    entries.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    try:
        _atomic_write(_index_path(), json.dumps(entries, indent=2, ensure_ascii=False))
    except Exception:
        pass
    return entries


def _load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.is_file():
        return rebuild_index()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return rebuild_index()
        return entries
    except Exception:
        return rebuild_index()


def _save_index(entries: list[dict[str, Any]]) -> None:
    try:
        _atomic_write(_index_path(), json.dumps(entries, indent=2, ensure_ascii=False))
    except Exception:
        pass


def _update_index_entry(workspace_id: str, **kwargs: Any) -> None:
    entries = _load_index()
    found = False
    for e in entries:
        if e.get("id") == workspace_id or e.get("slug") == workspace_id:
            e.update(kwargs)
            found = True
            break
    if found:
        entries.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
        _save_index(entries)
    else:
        rebuild_index()


def create_workspace(
    name: str,
    goal: str = "",
    kind: str = "code",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Create a new workspace folder with .ollambo/project.json and update central index."""
    root = _workspace_root()
    base_slug = slugify(name)
    slug = unique_slug(root, base_slug)
    proj_dir = root / slug
    proj_dir.mkdir(parents=True, exist_ok=True)

    ollambo_dir = proj_dir / ".ollambo"
    ollambo_dir.mkdir(parents=True, exist_ok=True)
    (ollambo_dir / "backups").mkdir(parents=True, exist_ok=True)

    wid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    chat_ids = [chat_id] if chat_id else []

    metadata = {
        "id": wid,
        "name": name,
        "slug": slug,
        "kind": kind,
        "goal": goal,
        "created": now,
        "updated": now,
        "chat_ids": chat_ids,
        "status": "active",
        "summary": "",
        "files": {},
    }

    pj_path = ollambo_dir / "project.json"
    _atomic_write(pj_path, json.dumps(metadata, indent=2, ensure_ascii=False))

    # Update index
    entries = _load_index()
    entries.insert(0, {
        "id": wid,
        "name": name,
        "slug": slug,
        "kind": kind,
        "goal": goal,
        "created": now,
        "updated": now,
        "chat_ids": chat_ids,
        "status": "active",
        "summary": "",
        "file_count": 0,
    })
    entries.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    _save_index(entries)

    return metadata


def get_workspace(workspace_id_or_slug: str) -> dict[str, Any] | None:
    """Retrieve full workspace metadata by ID or slug."""
    if not workspace_id_or_slug:
        return None
    root = _workspace_root()

    # Direct slug check first
    pj = root / workspace_id_or_slug / ".ollambo" / "project.json"
    if pj.is_file():
        try:
            return json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Check index
    entries = _load_index()
    for e in entries:
        if e.get("id") == workspace_id_or_slug or e.get("slug") == workspace_id_or_slug:
            slug = e.get("slug", "")
            pj_candidate = root / slug / ".ollambo" / "project.json"
            if pj_candidate.is_file():
                try:
                    return json.loads(pj_candidate.read_text(encoding="utf-8"))
                except Exception:
                    pass

    # Fallback scan all directories
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                pj = d / ".ollambo" / "project.json"
                if pj.is_file():
                    try:
                        data = json.loads(pj.read_text(encoding="utf-8"))
                        if data.get("id") == workspace_id_or_slug or data.get("slug") == workspace_id_or_slug:
                            rebuild_index()
                            return data
                    except Exception:
                        continue
    return None


def list_workspaces(kind: str | None = None) -> list[dict[str, Any]]:
    """List all workspaces newest first, optionally filtered by kind."""
    entries = _load_index()
    if kind is not None:
        entries = [e for e in entries if e.get("kind") == kind]
    entries.sort(key=lambda x: x.get("updated") or x.get("created") or "", reverse=True)
    return entries


def workspace_path(workspace_id_or_slug: str) -> Path | None:
    """Return the Path to the project root directory, or None if not found."""
    ws = get_workspace(workspace_id_or_slug)
    if not ws:
        return None
    slug = ws.get("slug")
    if not slug:
        return None
    p = _workspace_root() / slug
    return p if p.is_dir() else None


def list_files(workspace_id: str) -> list[str]:
    """List relative paths of project files, excluding internal and binary/oversized files."""
    p = workspace_path(workspace_id)
    if not p or not p.is_dir():
        return []

    result: list[str] = []
    ignored_dirs = {".ollambo", ".git", "__pycache__", "node_modules"}

    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        for file in files:
            full = Path(root) / file
            # Check size limit (> 5MB is excluded)
            try:
                st = full.stat()
                if st.st_size > 5_000_000:
                    continue
            except OSError:
                continue

            # Check binary extensions
            if full.suffix.lower() in _BINARY_EXTENSIONS:
                continue

            # Check for binary null bytes
            try:
                with open(full, "rb") as bf:
                    chunk = bf.read(1024)
                    if b"\x00" in chunk:
                        continue
            except Exception:
                continue

            rel = full.relative_to(p).as_posix()
            result.append(rel)

    result.sort()
    return result


def read_file(workspace_id: str, relpath: str, max_size: int = 500_000) -> str | None:
    """Read a file from workspace with safety check and size limit."""
    p = workspace_path(workspace_id)
    if not p:
        return None

    clean_rel = safe_relpath(relpath)
    target = (p / clean_rel).resolve()
    base_resolved = p.resolve()

    if not str(target).startswith(str(base_resolved)):
        raise ValueError(f"Path escapes workspace: {relpath}")

    if not target.is_file():
        return None

    try:
        with open(target, "rb") as f:
            raw = f.read(max_size)
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def write_file(workspace_id: str, relpath: str, content: str) -> bool:
    """Write content to relpath atomically. Backs up existing file before overwrite."""
    p = workspace_path(workspace_id)
    if not p:
        return False

    clean_rel = safe_relpath(relpath)
    target = (p / clean_rel).resolve()
    base_resolved = p.resolve()

    if not str(target).startswith(str(base_resolved)):
        raise ValueError(f"Path escapes workspace: {relpath}")

    # Backup if file already exists
    if target.is_file():
        backup_dir = p / ".ollambo" / "backups" / Path(clean_rel).parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        backup_path = backup_dir / f"{Path(clean_rel).name}.{ts}.bak"
        counter = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{Path(clean_rel).name}.{ts}_{counter}.bak"
            counter += 1
        try:
            shutil.copy2(target, backup_path)
        except Exception:
            pass

    # Atomic write to target
    _atomic_write(target, content)

    # Compute sha1 and size
    content_bytes = content.encode("utf-8")
    sha1 = hashlib.sha1(content_bytes).hexdigest()
    size = len(content_bytes)
    now = datetime.now().isoformat()

    # Update .ollambo/project.json
    pj = p / ".ollambo" / "project.json"
    if pj.is_file():
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
            if "files" not in meta or not isinstance(meta["files"], dict):
                meta["files"] = {}
            meta["files"][clean_rel] = {
                "size": size,
                "sha1": sha1,
                "updated": now,
            }
            meta["updated"] = now
            _atomic_write(pj, json.dumps(meta, indent=2, ensure_ascii=False))
            _update_index_entry(meta["id"], updated=now, file_count=len(meta["files"]))
        except Exception:
            pass

    return True


def read_all(
    workspace_id: str,
    max_tokens: int | None = None,
    prioritize: list[str] | None = None,
) -> dict[str, str]:
    """Return dictionary of {relpath: content} fitting within token budget."""
    files = list_files(workspace_id)
    if prioritize:
        pri_set = set(prioritize)
        ordered = [f for f in prioritize if f in files] + [f for f in files if f not in pri_set]
    else:
        ordered = files

    result: dict[str, str] = {}
    accumulated_tokens = 0

    for f in ordered:
        content = read_file(workspace_id, f)
        if content is None:
            continue
        toks = estimate_tokens(content)
        if max_tokens is not None and (accumulated_tokens + toks > max_tokens):
            continue
        accumulated_tokens += toks
        result[f] = content

    return result


def export_zip(workspace_id: str, dest: str | Path) -> bool:
    """Export workspace files to a zip archive, excluding .ollambo and VCS dirs."""
    p = workspace_path(workspace_id)
    if not p or not p.is_dir():
        return False

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    ignored_dirs = {".ollambo", ".git", "__pycache__", "node_modules"}

    try:
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
                for file in files:
                    full = Path(root) / file
                    rel = full.relative_to(p).as_posix()
                    zf.write(full, rel)
        return True
    except Exception:
        return False


def link_chat(workspace_id: str, chat_id: str) -> None:
    """Link a chat ID to the workspace."""
    p = workspace_path(workspace_id)
    if not p:
        return
    pj = p / ".ollambo" / "project.json"
    if pj.is_file():
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
            chat_ids = meta.get("chat_ids", [])
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
                meta["chat_ids"] = chat_ids
                meta["updated"] = datetime.now().isoformat()
                _atomic_write(pj, json.dumps(meta, indent=2, ensure_ascii=False))
                _update_index_entry(meta["id"], updated=meta["updated"], chat_ids=chat_ids)
        except Exception:
            pass


def rename_workspace(workspace_id: str, new_name: str) -> bool:
    """Rename a workspace in project.json and the central index."""
    p = workspace_path(workspace_id)
    if not p:
        return False
    pj = p / ".ollambo" / "project.json"
    if pj.is_file():
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
            meta["name"] = new_name
            now = datetime.now().isoformat()
            meta["updated"] = now
            _atomic_write(pj, json.dumps(meta, indent=2, ensure_ascii=False))
            _update_index_entry(meta["id"], name=new_name, updated=now)
            return True
        except Exception:
            return False
    return False
