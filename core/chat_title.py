"""
core/chat_title.py

Generates concise, descriptive conversation titles summarizing what the user
is discussing using a local Ollama model.
"""

from __future__ import annotations

import re
from typing import Any

from core.ollama_client import OllamaClient


SYSTEM_TITLE_PROMPT = (
    "You are a concise conversation title generator. Your job is to summarize what the user "
    "is discussing or asking about into a short, descriptive chat title (2 to 5 words).\n"
    "Rules:\n"
    "- Respond with ONLY the title itself on a single line.\n"
    "- Never include prefixes like 'Title:', 'Topic:', or 'Here is a title:'.\n"
    "- Never wrap the title in quotes, asterisks, or markdown formatting.\n"
    "- Do not end with a period or punctuation.\n"
    "- Make it specific to the user's topic (e.g. 'PostgreSQL Active Users Script' or 'React Router Setup')."
)

# Common prefixes models output despite system instructions
_PREFIX_REGEX = re.compile(
    r"^(?:(?:here(?:'s| is) (?:a |the )?(?:short |concise )?(?:suggested )?(?:chat )?title:?)|"
    r"(?:(?:chat |conversation )?(?:title|topic|subject|summary|name):?))\s*",
    re.IGNORECASE,
)

_QUOTE_CHARS = "\"'`“”‘’*#_-~"


def clean_title(raw_title: str, max_chars: int = 40) -> str:
    """Clean model output into a crisp, human-readable conversation title.

    Strips boilerplate prefixes, markdown formatting, quotes, and limits length.
    """
    if not raw_title or not isinstance(raw_title, str):
        return ""

    # Find the first non-empty line
    lines = [line.strip() for line in raw_title.splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[0]

    # Remove leading markdown heading hashes
    line = re.sub(r"^#+\s*", "", line)

    # Strip surrounding quotes and markdown formatting repeatedly
    line = line.strip(_QUOTE_CHARS).strip()

    # Strip prefixes like "Title: ...", "Topic: ...", "Here is a title: ..."
    line = _PREFIX_REGEX.sub("", line).strip()

    # Strip surrounding quotes again in case prefix had quotes inside
    line = line.strip(_QUOTE_CHARS).strip()

    # Collapse multiple whitespaces
    line = " ".join(line.split())

    # Strip trailing punctuation
    line = line.rstrip(".:,;!?- ")

    if not line:
        return ""

    # Truncate cleanly if too long
    if len(line) > max_chars:
        # Try cutting at the nearest word boundary
        truncated = line[:max_chars].rsplit(" ", 1)[0]
        if len(truncated) < max_chars // 2:
            truncated = line[:max_chars].strip()
        line = truncated + "…"

    return line


def generate_chat_title(
    prompt_or_messages: str | list[dict[str, str]],
    ollama_url: str = "",
    model: str = "",
    num_ctx: int = 2048,
    client: OllamaClient | None = None,
) -> str:
    """Ask Ollama to summarize the conversation topic into a short title.

    Returns the cleaned title, or an empty string if generation failed.
    """
    if isinstance(prompt_or_messages, list):
        user_texts = [
            m.get("content", "")
            for m in prompt_or_messages
            if m.get("role") == "user" and m.get("content")
        ]
        prompt_text = "\n".join(user_texts[:2]) if user_texts else ""
    else:
        prompt_text = str(prompt_or_messages or "")

    prompt_text = prompt_text.strip()
    if not prompt_text or not model:
        return ""

    from core.storage import get_configured_threads

    own_client = False
    if client is None:
        if not ollama_url:
            return ""
        client = OllamaClient(ollama_url)
        own_client = True

    try:
        opts: dict[str, Any] = {
            "num_predict": 24,
            "temperature": 0.3,
            "num_ctx": min(num_ctx, 4096),
        }
        threads = get_configured_threads()
        if threads is not None:
            opts["num_thread"] = threads

        user_prompt = f"User message:\n{prompt_text[:500]}\n\nDescriptive topic title:"

        chunks: list[str] = []
        for chunk in client.chat_stream(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_TITLE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            options=opts,
        ):
            if c := chunk.get("message", {}).get("content", ""):
                chunks.append(c)
            if chunk.get("done", False):
                break

        raw_result = "".join(chunks)
        cleaned = clean_title(raw_result, max_chars=40)
        return cleaned
    except Exception:
        return ""
    finally:
        if own_client:
            client.close()
