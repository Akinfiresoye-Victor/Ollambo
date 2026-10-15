"""
Structured planner for Plan-First Coding Mode.

Generates a structured plan (goal, assumptions, files, steps) before any
code is generated. Uses Ollama JSON format schema with robust fallbacks:
  1. Calls model with JSON format schema.
  2. If JSON parsing fails, retries once with a strict reminder.
  3. If parsing still fails, returns a fallback plan wrapping the raw text.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from core.ollama_client import OllamaClient
from core.prompts import PLANNER_JSON_RETRY, PLANNER_SYSTEM

# JSON Schema passed to Ollama's `format` parameter.
PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "goal": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "files": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "description": {"type": "string"},
                    "file": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify"]},
                },
                "required": ["step", "description", "file"],
            },
        },
    },
    "required": ["goal", "assumptions", "files", "steps"],
}


@dataclass
class PlanStep:
    step: int
    description: str
    file: str
    completed: bool = False
    action: str = "create"


@dataclass
class CodingPlan:
    goal: str
    assumptions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    raw_text: str = ""
    project_name: str = ""


def parse_plan_json(text: str) -> CodingPlan | None:
    """Parse JSON text into a CodingPlan object.

    Strips markdown code fences if present. Returns None if invalid.
    """
    cleaned = text.strip()
    # Strip markdown ```json ... ``` wrapper if model included it
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        # Try to locate the outermost JSON object braces { ... }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except Exception:
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    from core.workspace import slugify

    goal = str(data.get("goal", "Coding task"))
    project_name = str(data.get("project_name") or "").strip()
    if not project_name:
        project_name = slugify(goal)

    assumptions = [str(a) for a in data.get("assumptions", [])]
    files = [str(f) for f in data.get("files", [])]

    steps: list[PlanStep] = []
    raw_steps = data.get("steps", [])
    if isinstance(raw_steps, list):
        for idx, s in enumerate(raw_steps, start=1):
            if isinstance(s, dict):
                act = str(s.get("action", "create")).lower().strip()
                if act not in ("create", "modify"):
                    act = "create"
                steps.append(
                    PlanStep(
                        step=int(s.get("step", idx)),
                        description=str(s.get("description", f"Create {s.get('file', '')}")),
                        file=str(s.get("file", f"file_{idx}.py")),
                        action=act,
                    )
                )

    return CodingPlan(
        goal=goal,
        assumptions=assumptions,
        files=files,
        steps=steps,
        raw_text=text,
        project_name=project_name,
    )


def generate_plan(
    ollama_url: str,
    model: str,
    user_request: str,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
    client: OllamaClient | None = None,
    existing_context: str | None = None,
) -> CodingPlan:
    """Call Ollama to generate a structured coding plan with fallback retries."""
    own_client = False
    if client is None:
        client = OllamaClient(ollama_url)
        own_client = True

    system_prompt = PLANNER_SYSTEM
    if existing_context:
        system_prompt += (
            "\n\nExisting Project Context:\n"
            f"{existing_context}\n\n"
            "INSTRUCTIONS FOR EXISTING PROJECT:\n"
            "- This request extends the existing project above.\n"
            "- Keep the same project_name.\n"
            "- For existing files that need changes, set action to 'modify'.\n"
            "- For new files being added, set action to 'create'.\n"
            "- Do NOT recreate files that need no change."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request},
    ]

    from core.storage import get_configured_threads
    gen_opts: dict[str, Any] = {
        "num_ctx": num_ctx,
        "temperature": 0.2,
        "top_p": 0.9,
    }
    threads = get_configured_threads()
    if threads is not None:
        gen_opts["num_thread"] = threads

    try:
        # Attempt 1: Using JSON format schema
        response_chunks: list[str] = []
        for chunk in client.chat_stream(
            model=model,
            messages=messages,
            options=gen_opts,
            format_schema=PLAN_JSON_SCHEMA,
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return CodingPlan(goal="Cancelled", raw_text="Cancelled by user")
            content = chunk.get("message", {}).get("content", "")
            if content:
                response_chunks.append(content)
            if chunk.get("done", False):
                break

        full_output = "".join(response_chunks).strip()
        plan = parse_plan_json(full_output)
        if plan and plan.steps:
            return plan

        # Attempt 2: Fallback retry if parsing failed
        messages.append({"role": "assistant", "content": full_output})
        messages.append({"role": "user", "content": PLANNER_JSON_RETRY})

        retry_chunks: list[str] = []
        for chunk in client.chat_stream(
            model=model,
            messages=messages,
            options=gen_opts,
            format_schema="json",
            stop_flag=stop_flag,
        ):
            if stop_flag and stop_flag():
                return CodingPlan(goal="Cancelled", raw_text="Cancelled by user")
            content = chunk.get("message", {}).get("content", "")
            if content:
                retry_chunks.append(content)
            if chunk.get("done", False):
                break

        retry_output = "".join(retry_chunks).strip()
        plan = parse_plan_json(retry_output)
        if plan and plan.steps:
            return plan

        # Final Fallback: Return raw text plan rather than crashing
        return CodingPlan(
            goal=user_request[:60] + "…",
            assumptions=["Parsed from raw output fallback"],
            files=[],
            steps=[PlanStep(step=1, description="Execute task according to plan", file="output.txt")],
            raw_text=retry_output or full_output,
        )
    finally:
        if own_client:
            client.close()
