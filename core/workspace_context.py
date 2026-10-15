"""
core/workspace_context.py - Format workspace attachment context for LLM prompt injection.

Always re-reads project files from disk. Prioritizes entry points and files mentioned in prompt,
staying strictly within a token budget (at most 40% of num_ctx).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from core.context_manager import estimate_tokens
from core.workspace import get_workspace, list_files, read_file
import core.research_store as research_store

ENTRY_POINTS = {
    "main.py",
    "app.py",
    "index.html",
    "index.js",
    "main.js",
    "server.py",
    "run.py",
    "manage.py",
}


def build_attachment_context(
    workspace_ids: Sequence[str],
    user_prompt: str = "",
    budget_tokens: int = 3200,
) -> str:
    """Build a markdown context string summarizing attached workspaces or research projects.

    Prioritizes:
    1. Files mentioned in the user_prompt
    2. Entry point files (main.py, app.py, index.html, etc.)
    3. Smaller files before larger files
    Always lists every file name in the project file tree, even if content is omitted.
    For research projects: includes top ledger claims and report.md (trimmed to budget).
    """
    if not workspace_ids:
        return ""

    sections: list[str] = []
    p_lower = user_prompt.lower()
    used_tokens = 0

    for wid in workspace_ids:
        ws = get_workspace(wid)
        if not ws:
            # Check if this is a research project
            res_projects = {p.get("id"): p for p in research_store.list_projects()}
            if wid in res_projects:
                rp = res_projects[wid]
                title = rp.get("title") or rp.get("topic") or "Research"
                ledger = research_store.load_ledger(wid)
                report = research_store.load_report(wid)

                r_lines = [f"=== Attached Research: {title} ==="]
                if ledger:
                    r_lines.append("Top Ledger Claims:")
                    # Take top verified or high confidence claims (up to 10)
                    claims = ledger[:10]
                    for c in claims:
                        st = c.get("statement", "")
                        conf = c.get("confidence", "")
                        status = c.get("status", "")
                        r_lines.append(f"  - [{status}] {st} (Confidence: {conf})")
                    r_lines.append("")

                top_part = "\n".join(r_lines) + "\n"
                t_tok = estimate_tokens(top_part)
                if used_tokens + t_tok > budget_tokens:
                    sections.append(f"=== Attached Research: {title} ===\n")
                    break

                used_tokens += t_tok
                r_block = [top_part]

                if report:
                    r_block.append("Research Report:\n")
                    rep_tok = estimate_tokens(report)
                    rem_budget = budget_tokens - used_tokens
                    if rep_tok > rem_budget and rem_budget > 100:
                        # Trim report roughly to remaining budget
                        char_limit = rem_budget * 4
                        trimmed = report[:char_limit] + "\n...(report trimmed for context budget)..."
                        r_block.append(trimmed + "\n\n")
                        used_tokens += estimate_tokens(trimmed)
                    elif rep_tok <= rem_budget:
                        r_block.append(report + "\n\n")
                        used_tokens += rep_tok
                sections.append("".join(r_block))
            continue

        name = ws.get("name", "Untitled")
        slug = ws.get("slug", "")
        goal = ws.get("goal", "")
        files = list_files(wid)

        header_lines = [
            f"=== Attached Project: {name} (Folder: {slug}) ===",
        ]
        if goal:
            header_lines.append(f"Goal: {goal}")
        header_lines.append("Project Files:")
        if files:
            for f in files:
                header_lines.append(f"  - {f}")
        else:
            header_lines.append("  (empty workspace)")
        header_lines.append("")

        header_text = "\n".join(header_lines) + "\n"
        h_tokens = estimate_tokens(header_text)
        if used_tokens + h_tokens > budget_tokens:
            # If even the header exceeds budget, provide a minimal one-liner
            minimal = f"=== Attached Project: {name} ({len(files)} files) ===\n"
            sections.append(minimal)
            break

        used_tokens += h_tokens
        proj_blocks: list[str] = [header_text]

        # Prioritize files
        def _sort_key(relpath: str) -> tuple[int, int, str]:
            fname = Path(relpath).name.lower()
            is_mentioned = 0 if (fname in p_lower or relpath.lower() in p_lower) else 1
            is_entry = 0 if fname in ENTRY_POINTS else 1
            return (is_mentioned, is_entry, relpath)

        sorted_files = sorted(files, key=_sort_key)

        omitted = 0
        for rel in sorted_files:
            content = read_file(wid, rel)
            if content is None:
                continue

            ext = Path(rel).suffix.lstrip(".").lower() or "text"
            block = f"--- File: {rel} ---\n```{ext}\n{content}\n```\n\n"
            b_tokens = estimate_tokens(block)

            if used_tokens + b_tokens > budget_tokens:
                omitted += 1
                continue

            used_tokens += b_tokens
            proj_blocks.append(block)

        if omitted > 0:
            proj_blocks.append(
                f"({omitted} file(s) omitted to fit context budget; see Project Files list above)\n\n"
            )

        sections.append("".join(proj_blocks))

    return "\n".join(sections)
