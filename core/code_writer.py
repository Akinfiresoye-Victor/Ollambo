"""
Code Writer for Plan-First Coding Mode with Long Code Continuation.

Generates complete files one-by-one following the plan.
Features:
  - AST-based signature extraction for previously written files to save tokens.
  - Automatic continuation when done_reason is 'length' (model hit token limit).
  - Intelligent stitching removing duplicate overlap and stray code fences.
  - Multi-part generation tracking (Part X of file.py) with max continuation limits.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Callable

from core.ollama_client import OllamaClient
from core.planner import CodingPlan, PlanStep
from core.prompts import CODE_WRITER_SYSTEM, CODE_WRITER_USER, CONTINUATION_PROMPT


def extract_python_signatures(code: str) -> str:
    """Extract top-level class and function signatures using python's ast module."""
    try:
        tree = ast.parse(code)
    except Exception:
        lines = [line.strip() for line in code.splitlines() if line.strip()][:5]
        return "\n".join(lines) or "(Non-python or unparsed file)"

    signatures: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            signatures.append(f"class {node.name}:")
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    args = [a.arg for a in item.args.args]
                    signatures.append(f"    def {item.name}({', '.join(args)}): ...")
        elif isinstance(node, ast.FunctionDef):
            args = [a.arg for a in node.args.args]
            signatures.append(f"def {node.name}({', '.join(args)}): ...")

    return "\n".join(signatures) if signatures else "(No classes or functions defined)"


def build_project_summary(generated_files: dict[str, str]) -> str:
    """Build a compact 'project so far' summary for files already written."""
    if not generated_files:
        return "No files created yet."

    sections: list[str] = []
    for filename, code in generated_files.items():
        if filename.endswith(".py"):
            sigs = extract_python_signatures(code)
            sections.append(f"File `{filename}`:\n{sigs}")
        else:
            first_line = code.splitlines()[0] if code.splitlines() else ""
            sections.append(f"File `{filename}`: {first_line[:80]}")

    return "\n\n".join(sections)


def clean_code_fence(text: str) -> str:
    """Extract the raw code contents from markdown code fences ```...```."""
    cleaned = text.strip("\r\n")
    match = re.search(r"```[a-zA-Z0-9_\-\.\/]*\n(.*?)```", cleaned, re.DOTALL)
    if match:
        return match.group(1).rstrip()
    if cleaned.strip().startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
    if cleaned.rstrip().endswith("```"):
        last_idx = cleaned.rfind("```")
        cleaned = cleaned[:last_idx]
    return cleaned.rstrip()


def get_last_n_lines(text: str, n: int = 30) -> str:
    """Return the last n lines of text."""
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def stitch_continuation(original: str, continuation: str) -> str:
    """Stitch continuation text onto original text, removing duplicate lines and fences."""
    cont_clean = clean_code_fence(continuation).strip("\r\n")
    if not original:
        return cont_clean
    if not cont_clean:
        return original

    orig_lines = original.splitlines()
    cont_lines = cont_clean.splitlines()

    # Check for exact line overlap (e.g. model repeated the last 1-15 lines)
    max_check = min(15, len(orig_lines), len(cont_lines))
    for k in range(max_check, 0, -1):
        if orig_lines[-k:] == cont_lines[:k]:
            remaining = "\n".join(cont_lines[k:])
            return original.rstrip() + ("\n" + remaining if remaining else "")

    # Check if cut off mid-line and model completed or repeated that line
    if orig_lines and cont_lines:
        last_line = orig_lines[-1]
        first_line = cont_lines[0]
        if first_line.startswith(last_line) and len(last_line) > 3:
            cont_lines[0] = first_line[len(last_line):]
            remaining = "\n".join(cont_lines)
            return original + remaining

    return original.rstrip() + "\n" + cont_clean


def generate_single_file(
    ollama_url: str,
    model: str,
    plan: CodingPlan,
    step: PlanStep,
    generated_files: dict[str, str],
    num_ctx: int = 8192,
    max_continuations: int = 8,
    on_progress: Callable[[int, int], None] | None = None,
    stop_flag: Callable[[], bool] | None = None,
    client: OllamaClient | None = None,
) -> str:
    """Generate source code for a file, continuing automatically if output is cut off."""
    own_client = False
    if client is None:
        client = OllamaClient(ollama_url)
        own_client = True

    plan_desc = f"Goal: {plan.goal}\nSteps:\n" + "\n".join(
        f"{s.step}. {s.description} -> {s.file}" for s in plan.steps
    )
    signatures = build_project_summary(generated_files)

    prompt = CODE_WRITER_USER.format(
        plan=plan_desc,
        signatures=signatures,
        step=step.step,
        file=step.file,
    )

    system_msg = {"role": "system", "content": CODE_WRITER_SYSTEM}
    original_task_msg = {"role": "user", "content": prompt}
    messages = [system_msg, original_task_msg]

    # ── Initial Generation (Part 1) ───────────────────────────────────
    chunks: list[str] = []
    done_reason = "stop"

    from core.storage import get_configured_threads
    gen_options: dict[str, Any] = {
        "num_ctx": num_ctx,
        "temperature": 0.2,
        "top_p": 0.9,
    }
    threads = get_configured_threads()
    if threads is not None:
        gen_options["num_thread"] = threads

    try:
        for chunk in client.chat_stream(
            model=model,
            messages=messages,
            options=gen_options,
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return ""
            content = chunk.get("message", {}).get("content", "")
            if content:
                chunks.append(content)
            if chunk.get("done", False):
                done_reason = chunk.get("done_reason", "stop")
                break

        accumulated = clean_code_fence("".join(chunks))
        part = 1

        # ── Automatic Continuation Loop ───────────────────────────────────
        while done_reason == "length" and part < max_continuations:
            if stop_flag and stop_flag():
                break

            part += 1
            if on_progress:
                on_progress(part, max_continuations)

            last_lines = get_last_n_lines(accumulated, 30)
            cont_prompt = CONTINUATION_PROMPT.format(file=step.file, last_lines=last_lines)

            # Keep only the system prompt, original task, and last continuation prompt
            messages = [
                system_msg,
                original_task_msg,
                {"role": "user", "content": cont_prompt},
            ]

            chunks = []
            done_reason = "stop"

            for chunk in client.chat_stream(
                model=model,
                messages=messages,
                options=gen_options,
                stop_flag=stop_flag,
            ):
                if stop_flag and stop_flag():
                    return accumulated
                content = chunk.get("message", {}).get("content", "")
                if content:
                    chunks.append(content)
                if chunk.get("done", False):
                    done_reason = chunk.get("done_reason", "stop")
                    break

            accumulated = stitch_continuation(accumulated, "".join(chunks))

        return accumulated
    finally:
        if own_client:
            client.close()
