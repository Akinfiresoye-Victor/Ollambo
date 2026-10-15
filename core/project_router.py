"""
core/project_router.py - Route user requests to continue an existing project or start a new one.

Deterministic heuristic routing with LLM fallback for ambiguous cases.
"""

from __future__ import annotations

import re
from typing import Any

STRONG_NEW_PHRASES = [
    "new project",
    "start a new",
    "start fresh",
    "from scratch",
    "separate",
    "another project",
    "different project",
]

STRONG_CONTINUE_PHRASES = [
    "in this project",
    "same project",
    "continue",
    "keep going",
    "in the same",
]

EDIT_VERBS = [
    "add",
    "fix",
    "change",
    "update",
    "improve",
    "refactor",
    "rename",
    "remove",
    "tweak",
    "also",
    "now make it",
    "modify",
    "implement",
    "extend",
    "rewrite",
]

EXISTING_REFS = [
    "it",
    "this",
    "that",
    "the game",
    "the app",
    "the site",
    "this game",
    "this app",
    "this site",
    "existing",
]

BUILD_VERBS = [
    "build",
    "create",
    "make",
    "write",
    "generate",
    "develop",
]

PROJECT_NOUNS = [
    "website",
    "web page",
    "site",
    "game",
    "calculator",
    "app",
    "application",
    "tool",
    "cli",
    "bot",
    "script",
    "program",
    "api",
    "server",
    "dashboard",
    "platform",
    "portal",
    "system",
]

_STOP_WORDS = {
    "the", "a", "an", "of", "to", "in", "for", "on", "with", "and", "is", "it",
    "my", "project", "code", "file", "files", "py", "js", "html", "css", "json",
    "md", "please", "can", "you", "me", "want", "like", "need",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOP_WORDS}


def decide_project(
    prompt: str,
    workspace_summary: dict[str, Any] | None = None,
    has_attachment: bool = False,
) -> str:
    """Decide whether prompt should 'continue', start a 'new' project, or is 'ambiguous'.

    Rules:
    - If strong NEW phrase is present -> 'new'.
    - If user attached a project -> 'continue' (unless strong NEW was present).
    - If no active workspace in this chat -> 'new'.
    - If strong CONTINUE phrase is present -> 'continue'.
    - Word overlap with name, goal, or file names -> 'continue'.
    - Build verb + project noun with zero overlap -> 'ambiguous'.
    - Edit verb or existing ref without fresh build noun -> 'continue'.
    - Default if ambiguous signals -> 'ambiguous', else 'continue'.
    """
    p_lower = prompt.lower()

    # 1. Strong NEW phrases
    for phrase in STRONG_NEW_PHRASES:
        if phrase in p_lower:
            return "new"

    # 2. Project attachment
    if has_attachment:
        return "continue"

    # 3. No active workspace
    if not workspace_summary or not (
        workspace_summary.get("id") or workspace_summary.get("name")
    ):
        return "new"

    # 4. Strong CONTINUE phrases
    for phrase in STRONG_CONTINUE_PHRASES:
        if phrase in p_lower:
            return "continue"

    # 5. Extract tokens from workspace
    ws_name = str(workspace_summary.get("name", ""))
    ws_goal = str(workspace_summary.get("goal", ""))
    files = workspace_summary.get("files", [])
    if isinstance(files, dict):
        file_names = list(files.keys())
    else:
        file_names = list(files)

    ws_tokens = _tokenize(ws_name) | _tokenize(ws_goal)
    for fn in file_names:
        ws_tokens |= _tokenize(fn)

    prompt_tokens = _tokenize(prompt)
    overlap = ws_tokens & prompt_tokens

    # Check verbs and nouns
    has_build_verb = any(
        re.search(r"\b" + re.escape(v) + r"\b", p_lower) for v in BUILD_VERBS
    )
    has_project_noun = any(
        re.search(r"\b" + re.escape(n) + r"\b", p_lower) for n in PROJECT_NOUNS
    )
    has_edit_verb = any(
        re.search(r"\b" + re.escape(v) + r"\b", p_lower) if " " not in v else v in p_lower
        for v in EDIT_VERBS
    )
    has_existing_ref = any(
        re.search(r"\b" + re.escape(r) + r"\b", p_lower) if " " not in r else r in p_lower
        for r in EXISTING_REFS
    )

    # 6. Build verb + project noun with zero overlap -> 'ambiguous'
    if has_build_verb and has_project_noun and not overlap:
        return "ambiguous"

    # 7. Word overlap -> 'continue'
    if overlap:
        return "continue"

    # 8. Edit verbs or existing thing references -> 'continue'
    if has_edit_verb or has_existing_ref:
        return "continue"

    # 9. Build verb alone with no overlap -> 'ambiguous'
    if has_build_verb:
        return "ambiguous"

    # 10. Fallback to continue for existing workspace
    return "continue"


def route_with_llm(
    url: str,
    model: str,
    prompt: str,
    workspace_summary: dict[str, Any] | None,
    num_ctx: int = 8192,
) -> str:
    """Ask Ollama whether to continue or start a new project. Default to 'new' on failure."""
    if not workspace_summary:
        return "new"

    ws_name = workspace_summary.get("name", "Untitled")
    ws_goal = workspace_summary.get("goal", "")
    files = workspace_summary.get("files", [])
    if isinstance(files, dict):
        file_list = list(files.keys())
    else:
        file_list = list(files)
    file_str = ", ".join(file_list[:15]) if file_list else "None"

    question = (
        f"Existing project: {ws_name}\n"
        f"Goal: {ws_goal}\n"
        f"Files: {file_str}\n"
        f"New user request: {prompt}\n\n"
        "Should this request continue the existing project, or start a new separate project?\n"
        "Answer with exactly one word: 'continue' or 'new'."
    )

    try:
        from core.ollama_client import OllamaClient
        client = OllamaClient(url)
        resp = client.chat(
            model=model,
            messages=[{"role": "user", "content": question}],
            options={
                "num_ctx": num_ctx,
                "num_predict": 4,
                "temperature": 0.0,
            },
        )
        answer = (
            resp.get("message", {}).get("content", "")
            if isinstance(resp, dict)
            else str(resp)
        ).strip().lower()
        if "continue" in answer:
            return "continue"
        if "new" in answer:
            return "new"
    except Exception:
        pass
    return "new"


def classify_with_attachment(prompt: str) -> str:
    """When a project is attached, edit verbs mean 'code', anything else is 'chat'."""
    p_lower = prompt.lower()
    for ev in EDIT_VERBS:
        pattern = r"\b" + re.escape(ev) + r"\b" if " " not in ev else re.escape(ev)
        if re.search(pattern, p_lower):
            return "code"
    return "chat"
