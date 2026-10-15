"""Helpers for cleaning UI-only status and progress text."""

from __future__ import annotations

import re
from typing import Optional, Tuple


_CATEGORY_PATTERNS = (
    ("warn", re.compile(r"\b(error|failed|warning|disabled|couldn't|unable)\b", re.I)),
    ("search", re.compile(r"\b(search|searching|query|queries|lookup|looking up)\b", re.I)),
    ("fetch", re.compile(r"\b(fetch|fetching|download|downloading|request|retriev)\w*\b", re.I)),
    ("read", re.compile(r"\b(read|reading|parse|parsing|extract|extracting|source|page)\b", re.I)),
    ("check", re.compile(r"\b(check|checking|verify|verifying|validate|testing|test|fact-check)\b", re.I)),
)


def _is_emoji_codepoint(codepoint: int) -> bool:
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
    )


def _emoji_sequence_end(text: str, start: int) -> Optional[int]:
    if start >= len(text) or not _is_emoji_codepoint(ord(text[start])):
        return None

    index = start + 1
    while index < len(text):
        codepoint = ord(text[index])
        if (
            _is_emoji_codepoint(codepoint)
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xFE00 <= codepoint <= 0xFE0F
            or codepoint in (0x200D, 0x20E3)
        ):
            index += 1
        else:
            break
    return index


def _guess_category(text: str) -> Optional[str]:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text[:240]):
            return category
    return None


def strip_emoji_prefix(text: str) -> Tuple[str, Optional[str]]:
    """Remove leading emoji clusters and return a best-effort action category."""
    index = 0
    end = len(text)
    found_emoji = False

    while index < end:
        while index < end and text[index].isspace():
            index += 1
        sequence_end = _emoji_sequence_end(text, index)
        if sequence_end is None:
            break
        found_emoji = True
        index = sequence_end

    cleaned = text[index:].lstrip() if found_emoji else text
    return cleaned, _guess_category(cleaned)
