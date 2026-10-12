"""
Context Manager for tracking token usage and compacting long conversations.

Local models have limited context windows (num_ctx).
When a conversation exceeds ~70% of num_ctx, older messages are compacted
into a single concise summary message, preserving system prompts and the
most recent conversation turns.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from core.ollama_client import OllamaClient
from core.prompts import SUMMARIZER_SYSTEM, SUMMARIZER_USER


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token for English/code.

    Always returns at least 1 token for non-empty text.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate total tokens for a list of message dicts.

    Adds 4 tokens per message for JSON/role framing overhead.
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        total += estimate_tokens(content) + 4
    return total


def is_compaction_needed(
    estimated_tokens: int,
    num_ctx: int,
    threshold_ratio: float = 0.70,
) -> bool:
    """Return True if estimated token count exceeds the threshold (default 70%)."""
    return estimated_tokens >= int(num_ctx * threshold_ratio)


def sanitize_code_for_summary(text: str) -> str:
    """Replace large code blocks with brief placeholders so code isn't lost or summarized badly.

    Per spec: 'Never summarize generated code artifacts. Keep them stored and
    referenced by file name only.'
    """
    # Replace ```file.py\n...``` with [Code artifact: file.py]
    def _replace_block(match: re.Match) -> str:
        lang = match.group(1).strip() or "snippet"
        return f"[Code artifact: {lang}]"

    return re.sub(r"```([a-zA-Z0-9_\-\.\/]*)\n.*?```", _replace_block, text, flags=re.DOTALL)


def split_for_compaction(
    messages: list[dict[str, str]],
    keep_recent: int = 4,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Split messages into:

    1. system_msgs: System prompts that should never be dropped.
    2. to_summarize: Older messages to condense into one summary.
    3. recent_msgs: The most recent conversation turns kept intact.
    """
    system_msgs: list[dict[str, str]] = []
    dialogue_msgs: list[dict[str, str]] = []

    for m in messages:
        if m.get("role") == "system":
            system_msgs.append(m)
        else:
            dialogue_msgs.append(m)

    # If dialogue is too short to compact, keep everything as recent
    if len(dialogue_msgs) <= keep_recent:
        return system_msgs, [], dialogue_msgs

    to_summarize = dialogue_msgs[:-keep_recent]
    recent_msgs = dialogue_msgs[-keep_recent:]
    return system_msgs, to_summarize, recent_msgs


def build_compacted_messages(
    system_msgs: list[dict[str, str]],
    summary_text: str,
    recent_msgs: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Assemble the compacted message list for the model:

    [system prompts] + [summary note] + [recent messages]
    """
    result: list[dict[str, str]] = list(system_msgs)
    if summary_text.strip():
        result.append({
            "role": "system",
            "content": f"[Summary of earlier conversation:\n{summary_text.strip()}]",
        })
    result.extend(recent_msgs)
    return result


def fallback_truncate_messages(
    messages: list[dict[str, str]],
    keep_recent: int = 4,
) -> list[dict[str, str]]:
    """Fallback if the summarizer model fails: drops oldest dialogue turns."""
    sys_msgs, _, recent_msgs = split_for_compaction(messages, keep_recent=keep_recent)
    result = list(sys_msgs)
    if not recent_msgs and messages:
        return messages[-keep_recent:]
    result.extend(recent_msgs)
    return result


def build_extractive_summary(messages: list[dict[str, str]]) -> str:
    """Instant rule-based extractive summary of older dialogue turns (< 1ms).

    Ensures compaction is immediate and never hangs waiting on an LLM call.
    """
    if not messages:
        return ""

    items: list[str] = []
    for m in messages:
        role = m.get("role", "unknown")
        raw = sanitize_code_for_summary(m.get("content", "")).strip()
        if not raw:
            continue
        first_line = raw.splitlines()[0][:140].strip()
        if role == "user":
            items.append(f"• User: {first_line}")
        elif role == "assistant":
            items.append(f"• Assistant: {first_line}")

    if not items:
        return "Previous context discussed earlier in this session."

    return "Summary of earlier discussion:\n" + "\n".join(items[-8:])


def generate_summary(
    ollama_url: str,
    model: str,
    messages_to_summarize: list[dict[str, str]],
    stop_flag: Callable[[], bool] | None = None,
    timeout_seconds: float = 6.0,
) -> str:
    """Call Ollama to generate a concise summary with strict 6s timeout and instant extractive fallback."""
    if not messages_to_summarize:
        return ""

    fallback = build_extractive_summary(messages_to_summarize)

    # Format last few dialogue turns as compact text lines to avoid large prompt eval
    lines: list[str] = []
    for m in messages_to_summarize[-6:]:
        role = m.get("role", "unknown").capitalize()
        content = sanitize_code_for_summary(m.get("content", ""))
        lines.append(f"{role}: {content[:200]}")
    convo_text = "\n\n".join(lines)

    prompt = SUMMARIZER_USER.format(conversation=convo_text)
    req_messages = [
        {"role": "system", "content": SUMMARIZER_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    import os
    import time
    client = OllamaClient(ollama_url)
    collected: list[str] = []

    optimal_threads = min(8, max(2, os.cpu_count() or 4))
    start_time = time.time()

    try:
        for chunk in client.chat_stream(
            model=model,
            messages=req_messages,
            options={
                "num_ctx": 2048,
                "num_predict": 90,
                "temperature": 0.2,
                "top_p": 0.9,
                "num_thread": optimal_threads,
            },
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return fallback

            # Strict timeout guard: if LLM summary takes more than timeout_seconds, use instant fallback
            if time.time() - start_time > timeout_seconds:
                break

            content = chunk.get("message", {}).get("content", "")
            if content:
                collected.append(content)
            if chunk.get("done", False):
                break
    except Exception:
        return fallback
    finally:
        client.close()

    result = "".join(collected).strip()
    return result if result else fallback
