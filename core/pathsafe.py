"""
core/pathsafe.py

Pure path validation helper to prevent path traversal, drive letter escape,
and Windows reserved device names in generated file artifacts.
"""

from __future__ import annotations

import re

# Windows reserved device names (CON, PRN, AUX, NUL, COM1-COM9, LPT1-LPT9)
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_relpath(name: str) -> str:
    r"""Validate and normalize a relative file path.

    Rejects:
    - Empty or whitespace-only names
    - Absolute paths (starting with / or \)
    - Drive letters (e.g. C:, D:)
    - Parent directory traversal segments ('..')
    - Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)

    Returns:
        Clean relative path string with forward slashes (e.g. 'src/utils.py').

    Raises:
        ValueError: If the path is unsafe or invalid.
    """
    if not isinstance(name, str):
        raise ValueError("File path must be a string")

    raw = name.strip()
    if not raw:
        raise ValueError("File path cannot be empty")

    # Reject drive letters (e.g., C:\foo or C:foo)
    if re.match(r"^[a-zA-Z]:", raw):
        raise ValueError(f"Drive letters are not permitted: {name}")

    # Reject leading slashes (absolute paths)
    if raw.startswith("/") or raw.startswith("\\"):
        raise ValueError(f"Absolute paths are not permitted: {name}")

    # Normalize backslashes to forward slashes
    normalized = raw.replace("\\", "/")

    # Split into segments and validate each
    parts = normalized.split("/")
    cleaned_parts: list[str] = []

    for part in parts:
        part = part.strip()
        if not part or part == ".":
            continue

        if part == "..":
            raise ValueError(f"Path traversal ('..') is not permitted: {name}")

        # Check Windows reserved names (ignoring file extension)
        base = part.split(".")[0].upper()
        if base in _WINDOWS_RESERVED:
            raise ValueError(f"Windows reserved name '{base}' is not permitted: {name}")

        cleaned_parts.append(part)

    if not cleaned_parts:
        raise ValueError(f"Path contains no valid segments: {name}")

    return "/".join(cleaned_parts)
