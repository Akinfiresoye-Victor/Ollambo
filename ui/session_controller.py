"""One SessionController per conversation.

Owns that conversation's ChatSession, ChatView, and QThread workers.
Worker signals connect to bound @Slot methods (no lambdas) so tokens always
land on this session even if the user has switched to another chat.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from core.chat_session import ChatSession
from core.planner import CodingPlan
from ui.chat_view import ChatView
from ui.text_utils import strip_emoji_prefix
from ui.worker_guard import request_stop, worker_is_running


class SessionController(QObject):
    """Non-UI owner of one conversation's workers and transcript state."""

    activity = Signal()
    status_text = Signal(str)
    generating_progress = Signal(int)  # token count, for status line
    became_idle = Signal(object)  # self
    became_busy = Signal(object)  # self
    chat_complete = Signal(object, str, dict)  # self, full_content, meta
    plan_complete = Signal(object, object)  # self, CodingPlan
    worker_failed = Signal(object, str)  # self, err
    compaction_notice = Signal(object, int, int)  # self, old_tok, new_tok
    unread_changed = Signal(str, bool)  # chat_id, unread
    persist_needed = Signal(object)  # self
    classify_done = Signal(object, str)  # self, task_type
    route_done = Signal(object, str)  # self, decision

    def __init__(self, session: ChatSession, view: ChatView | None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.view = view
        self.coding_plan: CodingPlan | None = None
        self.plan_hybrid = False

        self.chat_worker: Any = None
        self.plan_worker: Any = None
        self.research_worker: Any = None
        self.classify_worker: Any = None
        self.route_worker: Any = None
        self.title_worker: Any = None

        self.visible = True
        self.classify_args: tuple | None = None
        self.route_args: tuple | None = None

    @property
    def chat_id(self) -> str:
        return self.session.id

    def is_busy(self) -> bool:
        if self.session.is_busy():
            return True
        return any(
            worker_is_running(w)
            for w in (
                self.chat_worker,
                self.plan_worker,
                self.research_worker,
                self.classify_worker,
                self.route_worker,
            )
        )

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if visible and self.session.unread:
            self.session.unread = False
            self.unread_changed.emit(self.chat_id, False)
            self.persist_needed.emit(self)

    def mark_unread_if_hidden(self) -> None:
        if not self.visible:
            self.session.unread = True
            self.unread_changed.emit(self.chat_id, True)

    # ── Worker binding (no lambdas) ───────────────────────────────────

    def bind_chat_worker(self, worker: Any) -> None:
        self.chat_worker = worker
        worker.token_received.connect(self.on_token)
        if hasattr(worker, "compaction_started"):
            worker.compaction_started.connect(self.on_compaction_started)
        if hasattr(worker, "compaction_performed"):
            worker.compaction_performed.connect(self.on_compaction_performed)
        worker.chat_finished.connect(self.on_chat_finished)
        worker.chat_error.connect(self.on_chat_error)
        worker.finished.connect(partial(self.on_chat_worker_thread_finished, worker))
        self.session.begin_assistant("generating")
        self.became_busy.emit(self)

    def bind_plan_worker(self, worker: Any, hybrid: bool = False) -> None:
        self.plan_worker = worker
        self.plan_hybrid = hybrid
        if hybrid:
            worker.plan_ready.connect(self.on_hybrid_plan_ready)
            worker.plan_error.connect(self.on_plan_error_ignored)
        else:
            worker.plan_ready.connect(self.on_plan_ready)
            worker.plan_error.connect(self.on_chat_error)
        worker.finished.connect(partial(self.on_plan_worker_thread_finished, worker))
        if self.session.status == "idle":
            self.session.status = "planning"
        self.became_busy.emit(self)

    def bind_classify_worker(self, worker: Any, args: tuple) -> None:
        self.classify_worker = worker
        self.classify_args = args
        self.session.status = "planning"
        worker.result_ready.connect(self.on_classify_result)
        worker.finished.connect(partial(self.on_classify_worker_thread_finished, worker))
        self.became_busy.emit(self)

    def bind_route_worker(self, worker: Any, args: tuple) -> None:
        self.route_worker = worker
        self.route_args = args
        self.session.status = "planning"
        worker.result_ready.connect(self.on_route_result)
        worker.finished.connect(partial(self.on_route_worker_thread_finished, worker))
        self.became_busy.emit(self)

    def bind_research_worker(self, worker: Any) -> None:
        self.research_worker = worker
        worker.error.connect(self.on_chat_error)
        worker.finished.connect(partial(self.on_research_worker_thread_finished, worker))
        self.session.status = "researching"
        self.became_busy.emit(self)

    def clear_chat_worker(self) -> None:
        self.chat_worker = None

    def clear_plan_worker(self) -> None:
        self.plan_worker = None

    def clear_classify_worker(self) -> None:
        self.classify_worker = None

    def clear_route_worker(self) -> None:
        self.route_worker = None

    def clear_research_worker(self) -> None:
        self.research_worker = None

    # ── Stream slots ──────────────────────────────────────────────────

    @Slot(str)
    def on_token(self, token: str) -> None:
        if self.session.status != "generating":
            return
        self.session.append_token(token)
        if self.view is not None:
            self.view.ensure_assistant_widget()
            self.view.mark_stream_dirty(self.session.current_stream_text())
        self.activity.emit()
        self.generating_progress.emit(len(self.session.streaming))

    @Slot(int, int)
    def on_compaction_started(self, current_tok: int, threshold_tok: int) -> None:
        self.status_text.emit(
            f"Compacting conversation ({current_tok:,} tokens exceeded 75% limit)…"
        )

    @Slot(list, int, int)
    def on_compaction_performed(self, new_messages: list, old_tok: int, new_tok: int) -> None:
        self.session.apply_compaction(new_messages)
        self.compaction_notice.emit(self, old_tok, new_tok)
        self.persist_needed.emit(self)
        self.status_text.emit(f"Compacted to {new_tok:,} tokens. Connecting to model…")

    @Slot(str, dict)
    def on_chat_finished(self, full_content: str, meta: dict) -> None:
        if self.view is not None:
            self.view.stop_stream_timer()
        msg = self.session.finish_assistant(full_content)
        if msg is None:
            # Worker finished after stop/switch-idle: do not duplicate.
            return
        if self.view is not None:
            if self.view.streaming_widget is not None:
                self.view.finish_stream(msg["content"])
            else:
                self.view.add_assistant(msg["content"])
        p, e = meta.get("prompt_eval_count", 0), meta.get("eval_count", 0)
        self.session.last_prompt_eval_tokens = (p + e) if p else 0
        if self.plan_hybrid and worker_is_running(self.plan_worker):
            self.session.status = "planning"
        self.mark_unread_if_hidden()
        self.persist_needed.emit(self)
        self.chat_complete.emit(self, msg["content"], meta)
        if not self.is_busy():
            self.became_idle.emit(self)

    @Slot(str)
    def on_chat_error(self, err_msg: str) -> None:
        if self.session.status not in ("generating", "planning", "researching"):
            return
        if self.view is not None:
            self.view.stop_stream_timer()
        clean_err, _ = strip_emoji_prefix(err_msg)
        text = f"Error: {clean_err}"
        self.session.add_error(text)
        if self.view is not None:
            if self.view.streaming_widget:
                self.view.finish_stream(text)
            else:
                self.view.add_assistant(text)
        self.mark_unread_if_hidden()
        self.persist_needed.emit(self)
        self.worker_failed.emit(self, err_msg)
        if not self.is_busy():
            self.became_idle.emit(self)

    @Slot(object)
    def on_plan_ready(self, plan: CodingPlan) -> None:
        if self.session.status != "planning":
            return
        self.coding_plan = plan
        self.session.status = "idle"
        self.plan_complete.emit(self, plan)
        if not self.is_busy():
            self.became_idle.emit(self)

    @Slot(object)
    def on_hybrid_plan_ready(self, plan: CodingPlan) -> None:
        if self.session.status not in ("planning", "generating"):
            return
        self.coding_plan = plan
        if not worker_is_running(self.chat_worker):
            self.session.status = "idle"
        self.plan_complete.emit(self, plan)
        if not self.is_busy():
            self.became_idle.emit(self)

    @Slot(str)
    def on_plan_error_ignored(self, _err: str) -> None:
        if not worker_is_running(self.chat_worker):
            self.session.status = "idle"
            self.persist_needed.emit(self)

    def on_chat_worker_thread_finished(self, worker: Any = None) -> None:
        if worker is not None and self.chat_worker is not worker:
            return
        self.chat_worker = None
        if not self.is_busy():
            self.became_idle.emit(self)

    def on_plan_worker_thread_finished(self, worker: Any = None) -> None:
        if worker is not None and self.plan_worker is not worker:
            return
        self.plan_worker = None
        if not self.is_busy():
            self.became_idle.emit(self)

    @Slot(str)
    def on_classify_result(self, task_type: str) -> None:
        if self.session.status != "planning":
            return
        self.session.status = "idle"
        self.classify_done.emit(self, task_type)

    @Slot(str)
    def on_route_result(self, decision: str) -> None:
        if self.session.status != "planning":
            return
        self.session.status = "idle"
        self.route_done.emit(self, decision)

    def on_classify_worker_thread_finished(self, worker: Any = None) -> None:
        if worker is not None and self.classify_worker is not worker:
            return
        self.classify_worker = None
        if not self.is_busy():
            self.became_idle.emit(self)

    def on_route_worker_thread_finished(self, worker: Any = None) -> None:
        if worker is not None and self.route_worker is not worker:
            return
        self.route_worker = None
        if not self.is_busy():
            self.became_idle.emit(self)

    def on_research_worker_thread_finished(self, worker: Any = None) -> None:
        if worker is not None and self.research_worker is not worker:
            return
        self.research_worker = None
        if not self.is_busy():
            self.became_idle.emit(self)

    def stop_chat_workers(self) -> None:
        """User-initiated stop. Does not run when merely switching chats."""
        if self.view is not None:
            self.view.stop_stream_timer()
        partial = self.session.stop_assistant()
        if self.view is not None:
            if partial:
                self.view.finish_stream(partial["content"], stopped=True)
            else:
                self.view.discard_stream_widget()
        for w in (
            self.chat_worker,
            self.plan_worker,
            self.classify_worker,
            self.route_worker,
            self.research_worker,
        ):
            request_stop(w)
        self.persist_needed.emit(self)
        self.became_idle.emit(self)

    def retry_last_turn(self) -> tuple[str, list[dict[str, Any]]]:
        if self.view is not None:
            self.view.remove_retry_button()
        prompt, attachments = self.session.pop_last_turn_for_retry()
        if self.view is not None:
            self.view.pop_last_message_widget("assistant")
            self.view.pop_last_message_widget("user")
        return prompt, attachments

    def persist_dict(self) -> dict[str, Any]:
        data = self.session.to_dict()
        if self.coding_plan:
            plan = self.coding_plan
            data["plan"] = {
                "goal": plan.goal,
                "assumptions": plan.assumptions,
                "files": plan.files,
                "project_name": getattr(plan, "project_name", ""),
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
                "raw_text": plan.raw_text,
            }
        return data
