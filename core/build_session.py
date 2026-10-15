"""
Build session manager — handles work session persistence, checkpointing,
and resume logic for the Plan-First Coding Mode.
"""

from __future__ import annotations

from typing import Any

from core.planner import CodingPlan, PlanStep
from core.storage import (
    delete_work_session, load_work_session, save_work_session,
)


def save_session(
    chat_id: str,
    plan: CodingPlan,
    build_files: dict[str, str],
    next_step: int,
    workspace_id: str | None = None,
) -> None:
    """Persist current build state to disk for crash recovery and resume."""
    session = {
        "chat_id": chat_id,
        "workspace_id": workspace_id,
        "project_name": getattr(plan, "project_name", ""),
        "goal": plan.goal,
        "assumptions": plan.assumptions,
        "files": plan.files,
        "steps": [
            {
                "step": s.step,
                "description": s.description,
                "file": s.file,
                "completed": s.completed,
                "action": getattr(s, "action", "create"),
            }
            for s in plan.steps
        ],
        "completed_files": build_files,
        "next_step": next_step,
        "completed": False,
    }
    save_work_session(session)


def load_session(chat_id: str) -> dict[str, Any] | None:
    """Load an incomplete work session for a given chat_id."""
    session = load_work_session(chat_id)
    if not session or session.get("completed", False):
        return None
    return session


def reconstruct_plan(session: dict[str, Any]) -> CodingPlan:
    """Rebuild a CodingPlan from a saved session dict."""
    steps = [
        PlanStep(
            step=s.get("step", i + 1),
            description=s.get("description", ""),
            file=s.get("file", ""),
            completed=s.get("completed", False),
            action=s.get("action", "create"),
        )
        for i, s in enumerate(session.get("steps", []))
    ]
    return CodingPlan(
        goal=session.get("goal", ""),
        assumptions=session.get("assumptions", []),
        files=session.get("files", []),
        steps=steps,
        raw_text=session.get("raw_text", ""),
        project_name=session.get("project_name", ""),
    )


def get_resume_step(plan: CodingPlan) -> int:
    """Return the step number to resume from (first non-completed step)."""
    completed = [s.step for s in plan.steps if s.completed]
    return (max(completed) + 1) if completed else 1


def complete_session(chat_id: str) -> None:
    """Mark a work session as fully completed and remove it."""
    delete_work_session(chat_id)
