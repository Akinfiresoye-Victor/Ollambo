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


@dataclass
class CodingPlan:
    goal: str
    assumptions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    raw_text: str = ""


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

    goal = str(data.get("goal", "Coding task"))
    assumptions = [str(a) for a in data.get("assumptions", [])]
    files = [str(f) for f in data.get("files", [])]

    steps: list[PlanStep] = []
    raw_steps = data.get("steps", [])
    if isinstance(raw_steps, list):
        for idx, s in enumerate(raw_steps, start=1):
            if isinstance(s, dict):
                steps.append(
                    PlanStep(
                        step=int(s.get("step", idx)),
                        description=str(s.get("description", f"Create {s.get('file', '')}")),
                        file=str(s.get("file", f"file_{idx}.py")),
                    )
                )

    return CodingPlan(
        goal=goal,
        assumptions=assumptions,
        files=files,
        steps=steps,
        raw_text=text,
    )


def generate_plan(
    ollama_url: str,
    model: str,
    user_request: str,
    num_ctx: int = 8192,
    stop_flag: Callable[[], bool] | None = None,
) -> CodingPlan:
    """Call Ollama to generate a structured coding plan with fallback retries."""
    client = OllamaClient(ollama_url)

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": user_request},
    ]

    optimal_threads = min(8, max(2, os.cpu_count() or 4))
    gen_opts = {
        "num_ctx": num_ctx,
        "temperature": 0.2,
        "top_p": 0.9,
        "num_thread": optimal_threads,
    }

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
