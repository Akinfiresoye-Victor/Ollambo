"""
Pure conversation model for one chat.

The session is the source of truth. Widgets only mirror it. No Qt imports —
this module must stay testable without a display.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

BUSY_STATUSES = ("generating", "planning", "researching", "building")
_SUMMARY_PREFIX = "[Summary of earlier conversation:"


class ChatSession:
    """In-memory state for a single conversation, including compaction metadata."""

    def __init__(self, id: str | None = None) -> None:
        self.id: str = id or str(uuid.uuid4())
        self.title: str | None = None
        self.ai_title: bool = False
        self.model: str = ""
        self.num_ctx: int = 8192
        self.messages: list[dict[str, Any]] = []
        self.streaming: list[str] = []
        self.status: str = "idle"
        self.summary_text: str = ""
        self.summary_upto: int = 0
        self.workspace_id: str | None = None
        self.research_project_id: str | None = None
        self.plan: dict[str, Any] | None = None
        self.unread: bool = False
        self.updated: float = time.time()
        self.last_user_prompt: str = ""
        self.last_user_attachments: list[dict[str, Any]] = []
        self.last_extra_system: str = ""
        self.draft_assistant: str | None = None
        self.workspace_ids_history: list[str] = []
        self.pending_project_target: dict[str, Any] | None = None
        self.build_files: dict[str, str] = {}
        self.last_prompt_eval_tokens: int = 0
        self.is_hybrid_mode: bool = False
        self.pending_attachments: list[dict[str, Any]] = []
        self.active_plan_data: dict[str, Any] | None = None
        self.pending_research_topic: str = ""
        self.pending_research_url: str = ""
        self.pending_research_model: str = ""
        self.pending_research_num_ctx: int = 8192
        self.pending_research_plan: Any = None
        self.pending_research_project_id: str | None = None
        self.research_activity_log: list[str] = []
        self.last_research_progress_time: float = 0.0
        self.research_start_mono: float = 0.0
        self.sequential_plan_args: tuple[Any, ...] | None = None

    def is_busy(self) -> bool:
        return self.status in BUSY_STATUSES

    def touch(self) -> None:
        self.updated = time.time()

    def add_user(self, content: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "user", "content": content}
        if attachments:
            msg["attachments"] = [
                {
                    key: a[key]
                    for key in ("id", "name", "kind")
                    if key in a
                }
                for a in attachments
            ]
        self.messages.append(msg)
        self.last_user_prompt = content
        self.last_user_attachments = list(attachments or [])
        self.unread = False
        self.touch()
        return msg

    def begin_assistant(self, status: str = "generating") -> None:
        self.streaming = []
        self.draft_assistant = None
        self.status = status
        self.unread = False
        self.touch()

    def append_token(self, token: str) -> None:
        # Tokens for a session that is no longer generating are ignored so a
        # stopped or switched-away worker cannot leak into the transcript.
        if self.status != "generating":
            return
        self.streaming.append(token)

    def current_stream_text(self) -> str:
        return "".join(self.streaming)

    def finish_assistant(self, full_content: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if self.status not in ("generating", "planning", "researching"):
            return None
        text = full_content if full_content is not None else self.current_stream_text()
        msg: dict[str, Any] = {"role": "assistant", "content": text}
        if extra:
            msg.update(extra)
        self.messages.append(msg)
        self.streaming = []
        self.draft_assistant = None
        self.status = "idle"
        self.touch()
        return msg

    def stop_assistant(self) -> dict[str, Any] | None:
        """Keep partial text as a stopped assistant message. Does not delete it."""
        text = self.current_stream_text() or self.draft_assistant or ""
        self.streaming = []
        self.draft_assistant = None
        self.status = "idle"
        self.touch()
        if not text:
            return None
        msg: dict[str, Any] = {"role": "assistant", "content": text, "stopped": True}
        self.messages.append(msg)
        return msg

    def add_error(self, err_msg: str) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": err_msg, "error": True}
        self.messages.append(msg)
        self.streaming = []
        self.draft_assistant = None
        self.status = "error"
        self.touch()
        return msg

    def model_messages(self) -> list[dict[str, str]]:
        """Messages to send to Ollama: optional summary + unsummarized tail."""
        out: list[dict[str, str]] = []
        if self.summary_text:
            out.append({
                "role": "system",
                "content": f"{_SUMMARY_PREFIX}\n{self.summary_text}]",
            })
        start = max(0, min(self.summary_upto, len(self.messages)))
        for m in self.messages[start:]:
            out.append({"role": str(m.get("role", "user")), "content": str(m.get("content", ""))})
        return out

    def apply_compaction(self, compacted: list[dict[str, Any]]) -> None:
        """Store summary metadata only. Never rewrite or delete session.messages."""
        summary_idx = -1
        summary_text = ""
        for i, m in enumerate(compacted):
            content = str(m.get("content", ""))
            if m.get("role") == "system" and content.startswith(_SUMMARY_PREFIX):
                summary_idx = i
                inner = content[len(_SUMMARY_PREFIX):].strip()
                if inner.endswith("]"):
                    inner = inner[:-1].strip()
                summary_text = inner
        if summary_idx < 0:
            return
        after = compacted[summary_idx + 1:]
        n_after = sum(1 for m in after if m.get("role") != "system")
        self.summary_text = summary_text
        self.summary_upto = max(0, len(self.messages) - n_after)
        self.touch()

    def pop_last_turn_for_retry(self) -> tuple[str, list[dict[str, Any]]]:
        """Remove the last assistant reply and the user prompt that produced it."""
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages.pop()
        prompt = ""
        attachments: list[dict[str, Any]] = []
        if self.messages and self.messages[-1].get("role") == "user":
            last = self.messages.pop()
            prompt = str(last.get("content", ""))
            raw = last.get("attachments") or []
            attachments = list(raw)
        self.streaming = []
        self.draft_assistant = None
        self.status = "idle"
        self.touch()
        return prompt, attachments

    def convert_draft_if_idle(self) -> None:
        """Crash recovery: leftover draft becomes a stopped assistant message."""
        draft = (self.draft_assistant or "").strip()
        if not draft:
            self.draft_assistant = None
            return
        if self.status in BUSY_STATUSES:
            return
        self.messages.append({"role": "assistant", "content": draft, "stopped": True})
        self.draft_assistant = None
        self.status = "idle"
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title or "New Chat",
            "ai_title": self.ai_title,
            "model": self.model,
            "num_ctx": self.num_ctx,
            "messages": list(self.messages),
            "workspace_id": self.workspace_id,
            "research_project_id": self.research_project_id,
            "summary_text": self.summary_text,
            "summary_upto": self.summary_upto,
            "updated": self.updated,
            "status": self.status,
            "unread": self.unread,
            "last_user_prompt": self.last_user_prompt,
            "last_user_attachments": list(self.last_user_attachments),
            "workspace_ids_history": list(self.workspace_ids_history),
            "pending_project_target": self.pending_project_target,
            "build_files": dict(self.build_files),
            "last_prompt_eval_tokens": self.last_prompt_eval_tokens,
            "is_hybrid_mode": self.is_hybrid_mode,
            "pending_attachments": list(self.pending_attachments),
            "pending_research_topic": self.pending_research_topic,
            "pending_research_url": self.pending_research_url,
            "pending_research_model": self.pending_research_model,
            "pending_research_num_ctx": self.pending_research_num_ctx,
            "pending_research_project_id": self.pending_research_project_id,
        }
        if self.plan:
            data["plan"] = self.plan
        if self.active_plan_data:
            data["active_plan_data"] = self.active_plan_data
        if self.draft_assistant:
            data["draft_assistant"] = self.draft_assistant
        elif self.status == "generating" and self.streaming:
            data["draft_assistant"] = self.current_stream_text()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatSession:
        session = cls(id=str(data.get("id") or uuid.uuid4()))
        session.title = data.get("title")
        session.ai_title = bool(data.get("ai_title", False))
        session.model = str(data.get("model") or "")
        try:
            session.num_ctx = int(data.get("num_ctx") or 8192)
        except (TypeError, ValueError):
            session.num_ctx = 8192
        session.messages = list(data.get("messages") or [])
        session.workspace_id = data.get("workspace_id")
        session.research_project_id = data.get("research_project_id")
        session.summary_text = str(data.get("summary_text") or "")
        try:
            session.summary_upto = int(data.get("summary_upto") or 0)
        except (TypeError, ValueError):
            session.summary_upto = 0
        session.plan = data.get("plan")
        session.updated = float(data.get("updated") or time.time())
        session.last_user_prompt = str(data.get("last_user_prompt") or "")
        session.last_user_attachments = list(data.get("last_user_attachments") or [])
        session.workspace_ids_history = list(data.get("workspace_ids_history") or [])
        session.pending_project_target = data.get("pending_project_target")
        session.build_files = dict(data.get("build_files") or data.get("artifacts") or {})
        session.pending_attachments = list(data.get("pending_attachments") or [])
        session.pending_research_topic = str(data.get("pending_research_topic") or "")
        session.pending_research_url = str(data.get("pending_research_url") or "")
        session.pending_research_model = str(data.get("pending_research_model") or "")
        try:
            session.pending_research_num_ctx = int(data.get("pending_research_num_ctx") or 8192)
        except (TypeError, ValueError):
            session.pending_research_num_ctx = 8192
        session.pending_research_project_id = data.get("pending_research_project_id")
        session.active_plan_data = data.get("active_plan_data")
        try:
            session.last_prompt_eval_tokens = int(data.get("last_prompt_eval_tokens") or 0)
        except (TypeError, ValueError):
            session.last_prompt_eval_tokens = 0
        session.is_hybrid_mode = bool(data.get("is_hybrid_mode", False))
        session.draft_assistant = data.get("draft_assistant") or None
        session.status = "idle"
        session.convert_draft_if_idle()
        return session
