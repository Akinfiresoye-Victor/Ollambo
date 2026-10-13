"""
workers/research_worker.py - Background QThread worker for Deep Research.

Signals:
  plan_ready(object, str)       -- (ResearchPlan, project_id)
  progress(str, int, int, str, str) -- (phase, step, total, msg, eta)
  status_message(str)           -- short status text for status bar
  section_written(str, str)     -- (section_title, section_markdown)
  finished(str)                 -- (project_id)
  error(str)                    -- (error_message)

Modes / Stages:
  "plan"     - Generate the research plan and emit plan_ready
  "execute"  - Execute engine (search, evidence, skeptic) then writer
  "resume"   - Resume interrupted project from stored checkpoint
  "auto"     - Complete end-to-end (if auto-approve enabled)
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient
from core.research_budget import NUM_PREDICT_SECTION
from core.research_engine import ResearchEngine
from core.research_planner import ResearchPlan, generate_research_plan
from core.research_store import (
    create_project,
    get_project,
    load_ledger,
    load_notes,
    load_plan,
    load_sources,
    load_status,
    save_plan,
    update_index_entry,
)
from core.research_writer import ResearchReportWriter


class ResearchWorker(QThread):
    """Deep Research background execution worker."""

    plan_ready = Signal(object, str)
    progress = Signal(str, int, int, str, str)  # phase, step, total, msg, eta
    status_message = Signal(str)
    section_written = Signal(str, str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        ollama_url: str,
        model: str,
        topic: str = "",
        num_ctx: int = 8192,
        depth: str = "standard",
        project_id: str | None = None,
        plan: ResearchPlan | None = None,
        stage: str = "auto",  # auto, plan, execute, resume
        auto_approve: bool = False,
        settings: dict[str, Any] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ollama_url = ollama_url
        self.model = model
        self.topic = topic
        self.num_ctx = num_ctx
        self.depth = depth.lower()
        self.project_id = project_id or ""
        self.plan = plan
        self.stage = stage
        self.auto_approve = auto_approve
        self.settings = settings or {}
        self._stop_flag = False
        self._start_time = 0.0

    def stop(self) -> None:
        """Cancel execution cleanly."""
        self._stop_flag = True

    def _is_stopped(self) -> bool:
        return self._stop_flag

    def _format_eta(self, step: int, total: int) -> str:
        if step <= 0 or total <= 0 or self._start_time == 0.0:
            return ""
        elapsed = time.time() - self._start_time
        rate = elapsed / step
        remaining = rate * (total - step)
        if remaining < 60:
            return f"~{int(remaining)}s left"
        return f"~{int(remaining / 60)}m left"

    def _on_engine_progress(self, phase: str, step: int, total: int, msg: str) -> None:
        eta = self._format_eta(step, total)
        self.progress.emit(phase, step, total, msg, eta)
        self.status_message.emit(f"[{phase}] {msg}")

    def run(self) -> None:
        self._start_time = time.time()
        client = OllamaClient(self.ollama_url)
        try:
            if self.stage == "resume":
                self._run_resume(client)
            elif self.stage == "execute":
                self._run_execute(client)
            elif self.stage == "plan":
                self._run_plan(client)
            else:  # auto
                if self.auto_approve:
                    self._run_auto(client)
                else:
                    self._run_plan(client)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            client.close()

    def _run_plan(self, client: OllamaClient) -> None:
        """Generate research plan and emit plan_ready."""
        self.status_message.emit("Drafting research plan…")
        self.progress.emit("Planning", 1, 1, "🧠 Formulating research questions and skeptic checklist with local LLM…", "")

        if not self.project_id:
            self.project_id = create_project(self.topic, self.depth)

        num_sq = 3 if self.depth == "quick" else (7 if self.depth == "thorough" else 5)
        plan = generate_research_plan(
            topic=self.topic,
            ollama_url=self.ollama_url,
            model=self.model,
            num_ctx=self.num_ctx,
            num_sq=num_sq,
            stop_flag=self._is_stopped,
            client=client,
        )

        if self._is_stopped():
            return

        self.plan = plan
        save_plan(self.project_id, plan.to_dict())
        update_index_entry(self.project_id, title=plan.main_question[:80])

        self.status_message.emit("Research plan ready for review.")
        self.plan_ready.emit(plan, self.project_id)

    def _run_execute(self, client: OllamaClient) -> None:
        """Execute research with an approved plan."""
        if not self.plan and self.project_id:
            plan_dict = load_plan(self.project_id)
            if plan_dict:
                self.plan = ResearchPlan.from_dict(plan_dict)

        if not self.plan:
            self.error.emit("No research plan available to execute.")
            return

        # 1. Run Research Engine
        engine = ResearchEngine(
            project_id=self.project_id,
            plan=self.plan,
            ollama_url=self.ollama_url,
            model=self.model,
            num_ctx=self.num_ctx,
            depth=self.depth,
            progress_cb=self._on_engine_progress,
            stop_flag=self._is_stopped,
            client=client,
        )
        engine.run_all()

        if self._is_stopped():
            return

        # 2. Run Report Writer
        self._run_writer(client, engine.sources, engine.ledger, engine.dropped_quotes_count)

    def _run_resume(self, client: OllamaClient) -> None:
        """Resume project from saved checkpoint."""
        if not self.project_id:
            self.error.emit("Cannot resume without a project ID.")
            return

        status = load_status(self.project_id)
        phase = status.get("phase", "planning")
        plan_dict = load_plan(self.project_id)
        if not plan_dict:
            self.error.emit("Cannot resume: project plan not found.")
            return

        self.plan = ResearchPlan.from_dict(plan_dict)
        sources = load_sources(self.project_id)
        ledger = load_ledger(self.project_id)
        dropped_quotes = status.get("dropped_quotes", 0)

        if phase == "writing":
            self._run_writer(client, sources, ledger, dropped_quotes)
        else:
            sq_idx = status.get("sq_index", 0)
            engine = ResearchEngine(
                project_id=self.project_id,
                plan=self.plan,
                ollama_url=self.ollama_url,
                model=self.model,
                num_ctx=self.num_ctx,
                depth=self.depth,
                progress_cb=self._on_engine_progress,
                stop_flag=self._is_stopped,
                client=client,
            )
            # Rehydrate engine state
            engine.sources = sources
            engine.sources_map = {s["id"]: s for s in sources if "id" in s}
            engine.ledger = ledger
            engine.notes = load_notes(self.project_id)
            engine.dropped_quotes_count = dropped_quotes

            engine.run_all(resume_from_sq=sq_idx + 1)
            if not self._is_stopped():
                self._run_writer(client, engine.sources, engine.ledger, engine.dropped_quotes_count)

    def _run_writer(
        self,
        client: OllamaClient,
        sources: list[dict],
        ledger: list[dict],
        dropped_quotes: int,
    ) -> None:
        """Run report writer."""
        writer = ResearchReportWriter(
            project_id=self.project_id,
            topic=self.topic or (self.plan.topic if self.plan else "Research"),
            plan_dict=self.plan.to_dict() if self.plan else {},
            sources=sources,
            ledger=ledger,
            client=client,
            model=self.model,
            num_ctx=self.num_ctx,
            dropped_quotes=dropped_quotes,
            progress_cb=self._on_engine_progress,
            section_cb=lambda title, md: self.section_written.emit(title, md),
            stop_flag=self._is_stopped,
        )
        writer.write_report()

        if not self._is_stopped():
            self.status_message.emit("Research report completed.")
            self.finished.emit(self.project_id)

    def _run_auto(self, client: OllamaClient) -> None:
        """Run end-to-end when auto-approve is active."""
        self._run_plan(client)
        if not self._is_stopped():
            self._run_execute(client)
