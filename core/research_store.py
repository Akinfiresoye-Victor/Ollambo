"""
core/research_store.py - Persistent storage for research projects.

Each project lives in: <app_data>/research/<id>/
  plan.json       - the approved research plan
  notes.json      - evidence notebook (claims per sub-question)
  ledger.json     - claim ledger with status/confidence
  sources.json    - all sources with credibility scores
  report.md       - the finished Markdown report
  status.json     - current phase + step (for resume)

One index file: <app_data>/research/index.json
  [{id, title, topic, date, status, source_count, depth, completed_sq, total_sq}]

All writes go through the same _atomic_write used by storage.py.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

# reuse atomic write logic without importing the full storage module
def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _research_root() -> Path:
    p = Path(user_data_dir("ollambo")) / "research"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _project_dir(project_id: str) -> Path:
    # Resolve folder using index entry 'folder', falling back to project_id for old projects
    root = _research_root()
    entries = _load_index()
    for e in entries:
        if e.get("id") == project_id:
            folder = e.get("folder")
            if folder:
                p = root / folder
                p.mkdir(parents=True, exist_ok=True)
                return p
    p = root / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return _research_root() / "index.json"


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def _load_index() -> list[dict]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(entries: list[dict]) -> None:
    _atomic_write(_index_path(), json.dumps(entries, ensure_ascii=False, indent=2))


def list_projects() -> list[dict]:
    """Return all project index entries, newest first."""
    return sorted(_load_index(), key=lambda x: x.get("date", ""), reverse=True)


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------

def create_project(topic: str, depth: str = "standard") -> str:
    """Create a new project folder and index entry. Returns the project id."""
    from core.workspace import slugify, unique_slug
    project_id = str(uuid.uuid4())
    root = _research_root()
    base_slug = slugify(topic)
    folder = unique_slug(root, base_slug)
    now = datetime.now().isoformat()
    entry = {
        "id": project_id,
        "folder": folder,
        "title": topic[:80],
        "topic": topic,
        "date": now,
        "status": "planning",
        "source_count": 0,
        "depth": depth,
        "completed_sq": 0,
        "total_sq": 0,
    }
    p = root / folder
    p.mkdir(parents=True, exist_ok=True)
    idx = _load_index()
    idx.append(entry)
    _save_index(idx)
    return project_id


def rename_folder(project_id: str, new_name: str) -> str | None:
    """Safely rename the project folder right after plan generation."""
    from core.workspace import slugify, unique_slug
    root = _research_root()
    base_slug = slugify(new_name)
    target_folder = unique_slug(root, base_slug)
    old_dir = _project_dir(project_id)
    new_dir = root / target_folder

    if old_dir.exists() and old_dir.resolve() != new_dir.resolve():
        try:
            old_dir.rename(new_dir)
        except Exception:
            try:
                shutil.move(str(old_dir), str(new_dir))
            except Exception:
                return None

    update_index_entry(project_id, folder=target_folder, title=new_name[:80])
    return target_folder


def update_index_entry(project_id: str, **kwargs: Any) -> None:
    """Update specific fields in the index for a project."""
    idx = _load_index()
    for entry in idx:
        if entry["id"] == project_id:
            entry.update(kwargs)
            break
    _save_index(idx)


def delete_project(project_id: str) -> None:
    """Delete a project folder and remove from index."""
    proj_dir = _project_dir(project_id)
    if proj_dir.exists():
        shutil.rmtree(proj_dir, ignore_errors=True)
    legacy = _research_root() / project_id
    if legacy.exists():
        shutil.rmtree(legacy, ignore_errors=True)
    idx = [e for e in _load_index() if e["id"] != project_id]
    _save_index(idx)


def rename_project(project_id: str, new_title: str) -> None:
    update_index_entry(project_id, title=new_title[:80])


def export_report(project_id: str, dest_path: str) -> bool:
    """Copy report.md to dest_path. Returns True on success."""
    src = _project_dir(project_id) / "report.md"
    if not src.exists():
        return False
    try:
        shutil.copy2(str(src), dest_path)
        return True
    except Exception:
        return False


def open_project_folder(project_id: str) -> None:
    """Open the project directory in the OS file manager."""
    import subprocess
    import sys
    folder = str(_project_dir(project_id))
    if sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


# ---------------------------------------------------------------------------
# Project data save/load
# ---------------------------------------------------------------------------

def save_plan(project_id: str, plan: dict) -> None:
    path = _project_dir(project_id) / "plan.json"
    _atomic_write(path, json.dumps(plan, ensure_ascii=False, indent=2))


def load_plan(project_id: str) -> dict | None:
    path = _project_dir(project_id) / "plan.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_notes(project_id: str, notes: list[dict]) -> None:
    path = _project_dir(project_id) / "notes.json"
    _atomic_write(path, json.dumps(notes, ensure_ascii=False, indent=2))


def load_notes(project_id: str) -> list[dict]:
    path = _project_dir(project_id) / "notes.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_ledger(project_id: str, ledger: list[dict]) -> None:
    path = _project_dir(project_id) / "ledger.json"
    _atomic_write(path, json.dumps(ledger, ensure_ascii=False, indent=2))


def load_ledger(project_id: str) -> list[dict]:
    path = _project_dir(project_id) / "ledger.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_sources(project_id: str, sources: list[dict]) -> None:
    path = _project_dir(project_id) / "sources.json"
    _atomic_write(path, json.dumps(sources, ensure_ascii=False, indent=2))


def load_sources(project_id: str) -> list[dict]:
    path = _project_dir(project_id) / "sources.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_report(project_id: str, markdown: str) -> None:
    path = _project_dir(project_id) / "report.md"
    _atomic_write(path, markdown)


def load_report(project_id: str) -> str:
    path = _project_dir(project_id) / "report.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def report_path(project_id: str) -> Path:
    return _project_dir(project_id) / "report.md"


def save_status(project_id: str, status: dict) -> None:
    """Save resume checkpoint: {phase, sq_index, section_index}."""
    path = _project_dir(project_id) / "status.json"
    _atomic_write(path, json.dumps(status, ensure_ascii=False, indent=2))


def load_status(project_id: str) -> dict:
    path = _project_dir(project_id) / "status.json"
    if not path.exists():
        return {"phase": "planning", "sq_index": 0, "section_index": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"phase": "planning", "sq_index": 0, "section_index": 0}


def get_project(project_id: str) -> dict | None:
    """Return the index entry for a single project."""
    for entry in _load_index():
        if entry["id"] == project_id:
            return entry
    return None
