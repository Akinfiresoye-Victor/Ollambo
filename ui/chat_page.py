"""
Main Chat Page — ChatGPT style interface with streaming, context compaction,
Plan-First Coding Mode, Long Code Continuation, Multi-Agent Generation,
Background Builds with Persistent Sessions, and Keep-Alive Model Connection.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import core.workspace as workspace
from core.code_writer import build_project_summary
from core.project_router import classify_with_attachment, decide_project, route_with_llm
from core.workspace_context import build_attachment_context
from core.build_session import complete_session, get_resume_step, load_session, reconstruct_plan, save_session
from core.context_manager import estimate_messages_tokens, is_compaction_needed
from core.ollama_client import OllamaClient
from core.planner import CodingPlan
from core.storage import (
    get_effective_keep_alive,
    get_effective_keep_alive_seconds,
    list_active_sessions,
    load_chat,
    resolve_num_ctx,
    save_chat,
)
from core.task_classifier import classify_task, classify_heuristic
from core.walkthrough import generate_project_walkthrough
from ui.artifacts_panel import ArtifactsPanel
from ui.chat_sidebar import ChatSidebar
from ui.chat_top_bar import ChatTopBar
from ui.message_widget import ChatMessageWidget
from ui.plan_panel import PlanPanel
from ui.research_panel import ResearchPanel
from ui.research_plan_panel import ResearchPlanPanel
from ui.settings_dialog import SettingsDialog
from core.web_research import extract_urls
from workers.chat_worker import ChatWorker
from workers.code_worker import CodeWorker
from workers.models_worker import ModelsWorker
from workers.plan_worker import PlanWorker
from workers.research_worker import ResearchWorker
from workers.web_answer_worker import WebAnswerWorker
from workers.title_worker import TitleWorker


class ContextLengthWorker(QThread):
    """Fetch model context length off the GUI thread to prevent freezing."""

    result_ready = Signal(int)

    def __init__(self, ollama_url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model

    def run(self) -> None:
        client = OllamaClient(self._url)
        ctx = client.get_context_length(self._model)
        client.close()
        self.result_ready.emit(ctx)


class ClassifyWorker(QThread):
    """Run LLM-based task classification off the GUI thread to prevent freezing."""

    result_ready = Signal(str)  # Emits 'chat', 'code', or 'both'

    def __init__(self, ollama_url: str, model: str, prompt: str, num_ctx: int = 8192, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._num_ctx = num_ctx

    def run(self) -> None:
        client = OllamaClient(self._url)
        try:
            result = classify_task(self._prompt, self._url, self._model, num_ctx=self._num_ctx, client=client)
            self.result_ready.emit(result)
        finally:
            client.close()


class RouteWorker(QThread):
    """Run LLM-based project routing off the GUI thread when heuristic is ambiguous."""

    result_ready = Signal(str)  # Emits 'continue' or 'new'

    def __init__(
        self,
        ollama_url: str,
        model: str,
        prompt: str,
        workspace_summary: dict | None,
        num_ctx: int = 8192,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt
        self._ws_summary = workspace_summary
        self._num_ctx = num_ctx

    def run(self) -> None:
        decision = route_with_llm(
            self._url,
            self._model,
            self._prompt,
            self._ws_summary,
            num_ctx=self._num_ctx,
        )
        self.result_ready.emit(decision)


class ChatInputEdit(QTextEdit):
    """Multiline input that sends on Enter and inserts a newline on Shift+Enter."""

    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class PreloadWorker(QThread):
    """Background thread to preload/warm a model into RAM/VRAM without freezing UI."""

    finished_preload = Signal(str, bool)

    def __init__(self, ollama_url: str, model: str, keep_alive: str = "3m", parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._keep_alive = keep_alive

    def run(self) -> None:
        client = OllamaClient(self._url)
        ok = client.preload_model(self._model, keep_alive=self._keep_alive)
        client.close()
        self.finished_preload.emit(self._model, ok)


class UnloadWorker(QThread):
    """Background thread to unload model when keep-alive window expires."""

    def __init__(self, ollama_url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model

    def run(self) -> None:
        client = OllamaClient(self._url)
        client.unload_model(self._model)
        client.close()


class ChatPage(QWidget):
    """Full interactive chat interface connected to local Ollama."""

    def __init__(self, settings: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._models: list[str] = []
        self._current_chat_id = str(uuid.uuid4())
        self._current_chat_title: str | None = None
        self._current_chat_has_ai_title: bool = False
        self._active_title_workers: list[TitleWorker] = []
        self._messages: list[dict] = []
        self._last_prompt_eval_tokens = 0

        self._active_chat_worker: ChatWorker | None = None
        self._active_plan_worker: PlanWorker | None = None
        self._active_code_worker: CodeWorker | None = None
        self._active_research_worker: ResearchWorker | None = None
        self._preload_worker: PreloadWorker | None = None
        self._classify_worker: ClassifyWorker | None = None

        self._current_assistant_widget: ChatMessageWidget | None = None
        self._current_assistant_text: list[str] = []

        # Track the last user prompt for retry functionality
        self._last_user_prompt: str = ""

        # Current chat's plan and files
        self._active_plan: CodingPlan | None = None
        self._build_files: dict[str, str] = {}

        # Background build tracking (decoupled from active chat view)
        self._build_running = False
        self._building_chat_id: str | None = None
        self._building_plan: CodingPlan | None = None
        self._building_files: dict[str, str] = {}

        # Workspace and Project State
        self._workspace_id: str | None = None
        self._workspace_ids_history: list[str] = []
        self._research_project_id: str | None = None
        self._pending_project_target: dict[str, Any] | None = None
        self._pending_attachments: list[dict[str, str]] = []
        self._last_user_attachments: list[dict[str, str]] = []
        self._last_extra_system: str = ""
        self._route_worker: RouteWorker | None = None

        self._is_hybrid_mode = False

        # Smooth ~100ms streaming UI flush timer to minimize CPU/VRAM load
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(100)
        self._stream_flush_timer.timeout.connect(self._flush_stream_ui)
        self._pending_stream_dirty = False

        self._user_scrolled_away = False

        # Debounced scroll-to-bottom to avoid excessive layout recalculations
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._do_scroll_to_bottom)

        self._build_ui()
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._refresh_models)
        self._auto_timer.start(25000)

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left Sidebar
        self.sidebar = ChatSidebar()
        self.sidebar.new_chat_clicked.connect(self._start_new_chat)
        self.sidebar.chat_selected.connect(self._load_conversation)
        self.sidebar.chat_deleted.connect(self._on_chat_deleted)
        self.sidebar.settings_clicked.connect(self._open_settings)
        self.sidebar.wip_clicked.connect(self._jump_to_wip)
        splitter.addWidget(self.sidebar)

        # Right Area
        right_area = QWidget()
        right_layout = QVBoxLayout(right_area)
        right_layout.setContentsMargins(12, 8, 12, 12)
        right_layout.setSpacing(6)

        # Top Bar
        self.top_bar = ChatTopBar()
        self.top_bar.model_changed.connect(self._on_model_changed)
        self.top_bar.refresh_clicked.connect(self._refresh_models)
        self.top_bar.mode_changed.connect(lambda m: self._settings.update({"coding_mode": m}))
        self.top_bar.keep_warm_requested.connect(self._on_keep_warm_requested)
        self.top_bar.connection_expired.connect(self._on_connection_expired)
        self.top_bar.set_mode(self._settings.get("coding_mode", "auto"))
        self.top_bar.set_keep_alive_seconds(get_effective_keep_alive_seconds(self._settings))
        self.top_bar.set_internet_access(bool(self._settings.get("internet_access", False)))
        right_layout.addWidget(self.top_bar)

        # Center Tabs
        self.tabs = QTabWidget()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        if sb := self.scroll_area.verticalScrollBar():
            sb.valueChanged.connect(self._on_scroll_value_changed)
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.addStretch()
        self.scroll_area.setWidget(self.history_container)

        self.plan_panel = PlanPanel()
        self.plan_panel.plan_approved.connect(self._on_plan_approved)
        self.plan_panel.plan_rejected.connect(lambda: self.tabs.setCurrentIndex(0))

        self.research_plan_panel = ResearchPlanPanel()
        self.research_plan_panel.plan_approved.connect(self._on_research_plan_approved)
        self.research_plan_panel.plan_cancelled.connect(lambda: self.tabs.setCurrentIndex(0))

        self.plan_stack = QStackedWidget()
        self.plan_stack.addWidget(self.plan_panel)
        self.plan_stack.addWidget(self.research_plan_panel)

        self.artifacts_panel = ArtifactsPanel()

        self.research_panel = ResearchPanel()
        self.research_panel.resume_requested.connect(self._resume_research)

        self.tabs.addTab(self.scroll_area, "💬 Chat")
        self.tabs.addTab(self.plan_stack, "📋 Plan")
        self.tabs.addTab(self.artifacts_panel, "📦 Artifacts")
        self.tabs.addTab(self.research_panel, "📚 Research")
        right_layout.addWidget(self.tabs)

        # Removable Attachment Chips Bar
        self.attachment_bar = QWidget()
        self.attachment_bar_layout = QHBoxLayout(self.attachment_bar)
        self.attachment_bar_layout.setContentsMargins(4, 2, 4, 2)
        self.attachment_bar_layout.setSpacing(6)
        self.attachment_bar.setVisible(False)
        right_layout.addWidget(self.attachment_bar)

        # Bottom Input Area
        input_row = QHBoxLayout()

        self.btn_attach = QToolButton()
        self.btn_attach.setText("➕")
        self.btn_attach.setToolTip("Attach an existing project or artifact")
        self.btn_attach.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_attach.setStyleSheet(
            "QToolButton { font-size: 15px; font-weight: bold; padding: 4px 8px; "
            "background-color: #282c34; border: 1px solid #3e4451; border-radius: 4px; color: #61afef; }"
            "QToolButton:hover { background-color: #3e4451; color: #ffffff; }"
        )
        self.attach_menu = QMenu(self)
        self.artifacts_submenu = QMenu("Artifacts", self.attach_menu)
        self.artifacts_submenu.aboutToShow.connect(self._populate_artifacts_submenu)
        self.attach_menu.addMenu(self.artifacts_submenu)
        self.btn_attach.setMenu(self.attach_menu)
        input_row.addWidget(self.btn_attach)

        self.input_edit = ChatInputEdit()
        self.input_edit.setPlaceholderText("Type a message or coding request… (Enter to send, Shift+Enter for new line)")
        self.input_edit.setMaximumHeight(80)
        self.input_edit.send_requested.connect(self._send_message)
        input_row.addWidget(self.input_edit)

        btn_box = QVBoxLayout()
        self.btn_send, self.btn_stop = QPushButton("Send"), QPushButton("Stop")
        self.btn_send.setStyleSheet("font-weight: bold; padding: 6px 12px;")
        self.btn_send.clicked.connect(self._send_message)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_generation)
        btn_box.addWidget(self.btn_send)
        btn_box.addWidget(self.btn_stop)
        input_row.addLayout(btn_box)

        right_layout.addLayout(input_row)
        splitter.addWidget(right_area)
        splitter.setSizes([200, 700])

    def set_models(self, models: list[str]) -> None:
        self._models = models
        self.top_bar.set_models(models, self._settings.get("default_model", ""))

    def _on_model_changed(self, name: str) -> None:
        if not name:
            return
        # Fetch context length in background to prevent GUI freeze
        url = self._settings.get("ollama_url", "http://localhost:11434")
        w = ContextLengthWorker(url, name, parent=self)
        w.result_ready.connect(lambda ctx: self._apply_context_length(ctx))
        w.finished.connect(w.deleteLater)
        w.start()

    def _apply_context_length(self, ctx: int) -> None:
        self._active_num_ctx = resolve_num_ctx(self._settings, model_max_ctx=ctx)
        self._update_token_meter()

    def _update_token_meter(self) -> None:
        mx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        self.top_bar.update_token_meter(self._last_prompt_eval_tokens or estimate_messages_tokens(self._messages), mx)

    def _refresh_models(self) -> None:
        # Avoid socket contention if active generation or code building is running
        if self._build_running or (self._active_chat_worker and self._active_chat_worker.isRunning()):
            return
        w = ModelsWorker(self._settings.get("ollama_url", "http://localhost:11434"), parent=self)
        w.models_ready.connect(self.set_models)
        w.finished.connect(w.deleteLater)
        w.start()

    # ── Keep-Alive & Model Warming ─────────────────────────────────────

    def _on_keep_warm_requested(self, model: str) -> None:
        url = self._settings.get("ollama_url", "http://localhost:11434")
        pw = self._preload_worker = PreloadWorker(
            url, model, keep_alive=get_effective_keep_alive(self._settings), parent=self
        )
        pw.finished_preload.connect(self._on_preload_finished)
        pw.start()

    def _on_preload_finished(self, model: str, success: bool) -> None:
        if success:
            self.top_bar.set_connected()
        else:
            self.top_bar.set_disconnected()

    def _on_connection_expired(self, model: str) -> None:
        url = self._settings.get("ollama_url", "http://localhost:11434")
        if model:
            uw = UnloadWorker(url, model, parent=self)
            uw.start()

    # ── Attachments Handling ───────────────────────────────────────────

    def _populate_artifacts_submenu(self) -> None:
        self.artifacts_submenu.clear()
        import core.workspace as workspace
        import core.research_store as research_store

        workspaces = workspace.list_workspaces()
        research_projects = research_store.list_projects()

        if not workspaces and not research_projects:
            act = self.artifacts_submenu.addAction("No artifacts yet")
            act.setEnabled(False)
            return

        if workspaces:
            for ws in workspaces:
                name = ws.get("name", "Untitled")
                cnt = ws.get("file_count", len(ws.get("files", {})))
                dt = ws.get("updated", "")[:10] or ws.get("created", "")[:10]
                act = self.artifacts_submenu.addAction(f"💻 {name} ({cnt} files, {dt})")
                wid = ws.get("id")
                act.triggered.connect(lambda checked=False, w_id=wid, w_name=name: self._add_attachment(w_id, w_name, "code"))

        if research_projects:
            for rp in research_projects:
                t = (rp.get("title") or rp.get("topic") or "Research")[:30]
                dt = rp.get("date", "")[:10]
                act = self.artifacts_submenu.addAction(f"🔬 {t} [Research] ({dt})")
                rid = rp.get("id")
                act.triggered.connect(lambda checked=False, r_id=rid, r_name=t: self._add_attachment(r_id, r_name, "research"))

    def _add_attachment(self, att_id: str, name: str, kind: str) -> None:
        if any(a["id"] == att_id for a in self._pending_attachments):
            return
        self._pending_attachments.append({"id": att_id, "name": name, "kind": kind})
        self._render_attachment_chips()

    def _remove_attachment(self, att_id: str) -> None:
        self._pending_attachments = [a for a in self._pending_attachments if a["id"] != att_id]
        self._render_attachment_chips()

    def _render_attachment_chips(self) -> None:
        while self.attachment_bar_layout.count():
            item = self.attachment_bar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._pending_attachments:
            self.attachment_bar.setVisible(False)
            return

        for att in self._pending_attachments:
            chip = QWidget()
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(6, 2, 6, 2)
            chip_layout.setSpacing(4)
            chip.setStyleSheet(
                "QWidget { background-color: #2c313a; border: 1px solid #3e4451; border-radius: 12px; }"
            )
            icon = "🔬 " if att.get("kind") == "research" else "📎 "
            lbl = QLabel(f"{icon}{att['name']}")
            lbl.setStyleSheet("color: #61afef; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            chip_layout.addWidget(lbl)

            btn_del = QPushButton("✕")
            btn_del.setFixedSize(16, 16)
            btn_del.setStyleSheet(
                "QPushButton { border: none; background: transparent; color: #abb2bf; font-size: 10px; font-weight: bold; } "
                "QPushButton:hover { color: #e06c75; }"
            )
            att_id = att["id"]
            btn_del.clicked.connect(lambda checked=False, a_id=att_id: self._remove_attachment(a_id))
            chip_layout.addWidget(btn_del)

            self.attachment_bar_layout.addWidget(chip)

        self.attachment_bar_layout.addStretch()
        self.attachment_bar.setVisible(True)

    # ── Chat & Tab Navigation ──────────────────────────────────────────

    def _start_new_chat(self) -> None:
        # Only save if there are actual messages to save (prevents ghost chats)
        if self._messages:
            self._save_current_conversation()

        # Stop any active chat/plan workers, but leave CodeWorker running in background!
        self._stop_chat_generation()

        self._current_chat_id = str(uuid.uuid4())
        self._current_chat_title = None
        self._current_chat_has_ai_title = False
        self._messages = []
        self._last_prompt_eval_tokens = 0
        self._active_plan = None
        self._build_files = {}
        self._last_user_prompt = ""
        self._workspace_id = None
        self._workspace_ids_history = []
        self._research_project_id = None
        self._pending_project_target = None
        self._pending_attachments = []
        self._last_user_attachments = []
        self._last_extra_system = ""
        self._render_attachment_chips()

        self._clear_history_ui()
        self.artifacts_panel.clear()
        self.sidebar.clear_selection()
        self._update_token_meter()

        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        self.tabs.setCurrentIndex(0)

    def _on_chat_deleted(self, chat_id: str) -> None:
        """Handle a chat being deleted from the sidebar."""
        if chat_id == self._current_chat_id:
            # The active chat was deleted — reset to a fresh state without saving
            self._stop_chat_generation()
            self._current_chat_id = str(uuid.uuid4())
            self._current_chat_title = None
            self._current_chat_has_ai_title = False
            self._messages = []
            self._last_prompt_eval_tokens = 0
            self._active_plan = None
            self._build_files = {}
            self._last_user_prompt = ""
            self._clear_history_ui()
            self.artifacts_panel.clear()
            self.sidebar.clear_selection()
            self._update_token_meter()
            self.plan_panel.btn_approve.setText("Approve & Start Coding")
            self.plan_panel.btn_approve.setEnabled(True)
            self.tabs.setCurrentIndex(0)

    def _clear_history_ui(self) -> None:
        while self.history_layout.count() > 1:
            if (item := self.history_layout.takeAt(0)) and item.widget():
                item.widget().deleteLater()

    def _load_conversation(self, chat_id: str) -> None:
        if chat_id == self._current_chat_id:
            if self._build_running or self._build_files:
                self.tabs.setCurrentIndex(2)
            return

        # Only save previous conversation if it has messages (prevents ghost chats)
        if self._messages:
            self._save_current_conversation()

        # Stop active chat/plan workers, NEVER kill background CodeWorker!
        self._stop_chat_generation()

        if not (data := load_chat(chat_id)):
            return

        self._current_chat_id = chat_id
        self._current_chat_title = data.get("title")
        self._current_chat_has_ai_title = bool(data.get("ai_title", False))
        self._messages = data.get("messages", [])
        self._last_prompt_eval_tokens = 0
        self._workspace_id = data.get("workspace_id")
        self._research_project_id = data.get("research_project_id")
        self._clear_history_ui()

        # Lazy-load: only show last 40 messages, add "Load earlier" button if needed
        self._lazy_load_offset = 0
        if len(self._messages) > 40:
            self._lazy_load_offset = len(self._messages) - 40
            self._add_load_earlier_button()
            visible = self._messages[self._lazy_load_offset:]
        else:
            visible = self._messages
        for msg in visible:
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget(msg["role"], msg["content"]))

        # Add retry button for the last assistant message
        self._add_retry_button()

        # If switching into the chat that is currently building in background:
        if self._build_running and self._building_chat_id == chat_id:
            self._active_plan = self._building_plan
            self._build_files = dict(self._building_files)
            self.artifacts_panel.clear()
            for fname, code in self._build_files.items():
                self.artifacts_panel.add_file(fname, code)
            if self._active_plan:
                self.plan_panel.set_plan(self._active_plan)
                for s in self._active_plan.steps:
                    if s.completed:
                        self.plan_panel.mark_step_completed(s.step)
            self.plan_panel.btn_approve.setText("Building in Progress…")
            self.plan_panel.btn_approve.setEnabled(False)
            self.tabs.setCurrentIndex(2)
        else:
            self.artifacts_panel.clear()
            old_artifacts = data.get("artifacts", {})

            if self._workspace_id:
                # Load files from the workspace on disk
                self._build_files = workspace.read_all(self._workspace_id)
                for fname, code in self._build_files.items():
                    self.artifacts_panel.add_file(fname, code)
                try:
                    self.artifacts_panel.load_workspace(self._workspace_id)
                except Exception:
                    pass
            elif old_artifacts:
                # Migration: old chat stored file contents inline — create a workspace for them
                plan_goal = ""
                if saved_plan := data.get("plan"):
                    plan_goal = saved_plan.get("goal", "")
                name = plan_goal or data.get("title") or "migrated-project"
                ws = workspace.create_workspace(name, goal=plan_goal, kind="code", chat_id=chat_id)
                self._workspace_id = ws.get("id")
                for fname, code in old_artifacts.items():
                    try:
                        workspace.write_file(self._workspace_id, fname, code)
                    except Exception:
                        pass
                self._build_files = dict(old_artifacts)
                for fname, code in self._build_files.items():
                    self.artifacts_panel.add_file(fname, code)
                # Persist the migrated workspace_id and clear old artifacts
                data["workspace_id"] = self._workspace_id
                data["artifacts"] = {}
                save_chat(data)
            else:
                self._build_files = {}

            if saved_plan := data.get("plan"):
                self._active_plan = reconstruct_plan(saved_plan)
                self.plan_panel.set_plan(self._active_plan)
                for s in self._active_plan.steps:
                    if s.completed:
                        self.plan_panel.mark_step_completed(s.step)
                all_done = all(s.completed for s in self._active_plan.steps)
                if all_done:
                    self.plan_panel.btn_approve.setText("✅ Build Completed")
                    self.plan_panel.btn_approve.setEnabled(False)
                else:
                    self.plan_panel.btn_approve.setText("▶ Resume Building")
                    self.plan_panel.btn_approve.setEnabled(True)
            else:
                self._active_plan = None
                self.plan_panel.btn_approve.setText("Approve & Start Coding")
                self.plan_panel.btn_approve.setEnabled(True)

            if session := load_session(chat_id):
                self._restore_session(session)
                self.tabs.setCurrentIndex(2)
            else:
                self.tabs.setCurrentIndex(2 if self._build_files else 0)

        self._update_token_meter()
        self._scroll_to_bottom()

    def _restore_session(self, session: dict) -> None:
        """Restore a saved work session: show artifacts, mark plan steps, offer resume."""
        plan = reconstruct_plan(session)
        self._active_plan = plan
        # Prefer workspace files on disk; fall back to session's completed_files
        ws_id = session.get("workspace_id") or self._workspace_id
        if ws_id:
            self._workspace_id = ws_id
            self._build_files = workspace.read_all(ws_id)
        else:
            self._build_files = dict(session.get("completed_files", {}))
        self.artifacts_panel.clear()
        for fname, code in self._build_files.items():
            self.artifacts_panel.add_file(fname, code)
        if ws_id:
            try:
                self.artifacts_panel.load_workspace(ws_id)
            except Exception:
                pass
        self.plan_panel.set_plan(plan)
        for s in plan.steps:
            if s.completed:
                self.plan_panel.mark_step_completed(s.step)
        all_done = all(s.completed for s in plan.steps)
        if all_done:
            self.plan_panel.btn_approve.setText("✅ Build Completed")
            self.plan_panel.btn_approve.setEnabled(False)
        else:
            self.plan_panel.btn_approve.setText("▶ Resume Building")
            self.plan_panel.btn_approve.setEnabled(True)

    def _jump_to_wip(self) -> None:
        target_id = self.sidebar._wip_chat_id
        if not target_id:
            active = list_active_sessions()
            if active:
                target_id = active[0]
        if not target_id:
            return
        if target_id == self._current_chat_id:
            self.tabs.setCurrentIndex(2)
        else:
            self._load_conversation(target_id)
            self.tabs.setCurrentIndex(2)

    # ── Retry Button ───────────────────────────────────────────────────

    def _add_retry_button(self) -> None:
        """Add a retry button below the last assistant message."""
        # Remove any existing retry row first
        self._remove_retry_button()

        # Find the last user message for retry content
        last_user_msg = ""
        for m in reversed(self._messages):
            if m["role"] == "user":
                last_user_msg = m["content"]
                break

        if not last_user_msg:
            return

        self._last_user_prompt = last_user_msg

        retry_row = QWidget()
        retry_row.setObjectName("retry_row")
        retry_layout = QHBoxLayout(retry_row)
        retry_layout.setContentsMargins(0, 2, 0, 6)
        retry_layout.setSpacing(8)

        btn_retry = QPushButton("🔄 Retry Request")
        btn_retry.setToolTip("Re-run the last prompt")
        btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_retry.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 4px 14px; background: #282c34; "
            "color: #abb2bf; border: 1px solid #3e4451; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3e4451; color: #ffffff; border-color: #61afef; }"
        )
        btn_retry.clicked.connect(self._on_retry_clicked)
        retry_layout.addWidget(btn_retry)
        retry_layout.addStretch()

        # Insert before the stretch (last item in history_layout)
        self.history_layout.insertWidget(self.history_layout.count() - 1, retry_row)

    def _remove_retry_button(self) -> None:
        """Remove existing retry button row if present."""
        for i in range(self.history_layout.count() - 1, -1, -1):
            item = self.history_layout.itemAt(i)
            if item and item.widget() and item.widget().objectName() == "retry_row":
                w = self.history_layout.takeAt(i).widget()
                if w:
                    w.deleteLater()

    def _on_retry_clicked(self) -> None:
        """Re-run the last user prompt by removing the old response and resending."""
        if not self._last_user_prompt:
            return

        # Remove the last assistant message from history (if any)
        if self._messages and self._messages[-1]["role"] == "assistant":
            self._messages.pop()

        # Remove the retry button and last assistant widget from UI
        self._remove_retry_button()

        # Remove the last assistant widget from the visual layout
        for i in range(self.history_layout.count() - 1, -1, -1):
            item = self.history_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessageWidget):
                widget = item.widget()
                if widget.role == "assistant":
                    self.history_layout.takeAt(i)
                    widget.deleteLater()
                    break

        # Put the prompt back in the input and trigger send
        self.input_edit.setPlainText(self._last_user_prompt)

        # Also remove the user message since _send_message will re-add it
        if self._messages and self._messages[-1]["role"] == "user":
            self._messages.pop()
            # Remove last user widget too
            for i in range(self.history_layout.count() - 1, -1, -1):
                item = self.history_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), ChatMessageWidget):
                    widget = item.widget()
                    if widget.role == "user":
                        self.history_layout.takeAt(i)
                        widget.deleteLater()
                        break

        # Restore attachments from the retried message so they re-apply
        if hasattr(self, "_last_user_attachments") and self._last_user_attachments:
            self._pending_attachments = list(self._last_user_attachments)
            self._render_attachment_chips()

        self._send_message()

    def _build_attachment_context(self, num_ctx: int) -> str:
        """Build a system-context string from pending attachments, capped at 40% of num_ctx."""
        if not self._pending_attachments:
            return ""
        budget = max(200, int(num_ctx * 0.40))
        ids = [a["id"] for a in self._pending_attachments]
        names = " ".join(a.get("name", "") for a in self._pending_attachments)
        return build_attachment_context(ids, user_prompt=names, budget_tokens=budget)

    def _strip_attachments_from_messages(self, messages: list[dict]) -> list[dict]:
        """Return a copy of messages with the 'attachments' key removed (never sent to Ollama)."""
        return [{k: v for k, v in m.items() if k != "attachments"} for m in messages]

    # ── Sending & Processing Messages ──────────────────────────────────

    def _send_message(self) -> None:
        text = self.input_edit.toPlainText().strip()
        model = self.top_bar.current_model()
        if not text or not model:
            return

        self.input_edit.clear()
        self._last_user_prompt = text

        # Capture attachments for this message (and restore on retry)
        attachments = list(self._pending_attachments)
        self._last_user_attachments = attachments
        self._pending_attachments = []
        self._render_attachment_chips()

        # Build attachment context (capped at 40% of num_ctx) for injection into the chat
        url = self._settings.get("ollama_url", "http://localhost:11434")
        num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        if attachments:
            budget = max(200, int(num_ctx * 0.40))
            ids = [a["id"] for a in attachments]
            names = " ".join(a.get("name", "") for a in attachments)
            self._last_extra_system = build_attachment_context(ids, user_prompt=names, budget_tokens=budget)
        else:
            self._last_extra_system = ""

        # Remove any existing retry button before adding new messages
        self._remove_retry_button()

        user_msg: dict[str, Any] = {"role": "user", "content": text}
        if attachments:
            user_msg["attachments"] = [{"id": a["id"], "name": a["name"]} for a in attachments]
        self._messages.append(user_msg)
        att_for_widget = attachments or None
        self.history_layout.insertWidget(
            self.history_layout.count() - 1, ChatMessageWidget("user", text, attachments=att_for_widget)
        )
        self._save_current_conversation()

        self.top_bar.set_working()
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self._current_assistant_text = []

        # AI title is requested after the main reply finishes (see _on_chat_finished / _on_plan_ready)
        # to avoid model reload races when num_ctx differs.

        est_tokens = estimate_messages_tokens(self._messages)
        if self._last_extra_system:
            est_tokens += estimate_messages_tokens([{"role": "system", "content": self._last_extra_system}])
        if is_compaction_needed(est_tokens, num_ctx, threshold_ratio=0.75):
            self.top_bar.show_status(f"⚡ Checking tokens ({est_tokens:,}/{num_ctx:,}) — compacting history…", 30)

        mode = self.top_bar.current_mode()
        extracted_urls = extract_urls(text)
        if mode in ("auto", "chat") and extracted_urls:
            if not self._settings.get("internet_access", False):
                err = (
                    "🌐 **Web access is disabled in Settings.**\n\n"
                    "Enable **Internet Access** in Settings to allow fetching and analyzing web pages."
                )
                self._current_assistant_widget = ChatMessageWidget("assistant", err)
                self.history_layout.insertWidget(
                    self.history_layout.count() - 1, self._current_assistant_widget
                )
                self._current_assistant_widget.set_content(err, is_final=True)
                self._messages.append({"role": "assistant", "content": err})
                self._save_current_conversation()
                self._cleanup_workers_state()
                self._scroll_to_bottom(force=True)
                return
            else:
                self._current_assistant_widget = ChatMessageWidget("assistant", "")
                self.history_layout.insertWidget(
                    self.history_layout.count() - 1, self._current_assistant_widget
                )
                opts = dict(self._settings)
                opts["num_ctx"] = num_ctx
                waw = self._active_chat_worker = WebAnswerWorker(
                    url, model, text, extracted_urls, options=opts, parent=self
                )
                waw.status_message.connect(self.top_bar.show_status)
                waw.token_received.connect(self._on_token_received)
                waw.chat_finished.connect(self._on_chat_finished)
                waw.chat_error.connect(self._on_worker_error)
                waw.finished.connect(waw.deleteLater)
                waw.start()
                self._scroll_to_bottom(force=True)
                return

        if mode == "code":
            self._dispatch_task("code", text, url, model, num_ctx)
        elif mode == "chat":
            self._dispatch_task("chat", text, url, model, num_ctx)
        elif mode == "research":
            if not self._settings.get("internet_access", False):
                self._show_internet_blocked_error()
                return
            self._dispatch_research(text, url, model, num_ctx)
        else:
            # Auto mode: if a project is attached, decide code vs chat via classify_with_attachment
            if attachments:
                att_task = classify_with_attachment(text)
                self._dispatch_task(att_task, text, url, model, num_ctx)
            else:
                # Auto mode: try fast heuristic first, only use LLM off-thread if ambiguous
                heuristic_result = classify_heuristic(text)
                if heuristic_result:
                    # Heuristic was decisive — dispatch immediately, no freeze
                    self._dispatch_task(heuristic_result, text, url, model, num_ctx)
                else:
                    # Ambiguous — run LLM classifier in background thread (prevents 5-15s freeze)
                    self.top_bar.show_status("🤔 Analyzing task type…")
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                    cw = self._classify_worker = ClassifyWorker(url, model, text, num_ctx, parent=self)
                    cw.result_ready.connect(lambda task_type: self._on_classify_done(task_type, text, url, model, num_ctx))
                    cw.finished.connect(cw.deleteLater)
                    cw.start()

        self._scroll_to_bottom(force=True)

    def _show_internet_blocked_error(self) -> None:
        """Show an inline error when Research is selected but internet is disabled."""
        err = (
            "🌐 **Web Research is disabled.**\n\n"
            "Enable it in **Settings > Internet Access** to use Research mode.\n\n"
            "Ollambo is offline by default for privacy — web access is opt-in."
        )
        if not self._current_assistant_widget:
            self._current_assistant_widget = ChatMessageWidget("assistant", err)
            self.history_layout.insertWidget(
                self.history_layout.count() - 1, self._current_assistant_widget
            )
        else:
            self._current_assistant_widget.set_content(err, is_final=True)
        self._cleanup_workers_state()

    def _dispatch_research(self, question: str, url: str, model: str, num_ctx: int) -> None:
        """Launch ResearchWorker to draft research plan or run deep research."""
        depth = self.top_bar.research_depth()
        auto_approve = bool(self._settings.get("auto_approve_research_plans", False))

        # Continue-or-new routing for research projects
        stage = "auto" if auto_approve else "plan"
        project_id = None
        if self._research_project_id:
            try:
                from core.research_store import get_project, load_plan

                proj = get_project(self._research_project_id)
                plan_data = load_plan(self._research_project_id) if proj else None
                if proj and plan_data:
                    main_q = plan_data.get("main_question", "")
                    sub_qs = [sq.get("question", "") for sq in plan_data.get("sub_questions", [])]
                    rs_summary = {
                        "name": proj.get("name", ""),
                        "goal": main_q,
                        "files": sub_qs,
                    }
                    decision = decide_project(question, rs_summary, has_attachment=False)
                    if decision == "continue":
                        stage = "continue"
                        project_id = self._research_project_id
                        self._add_notice_row(f"🔬 Continuing research on **{proj.get('name', main_q)}**")
                    else:
                        # new or ambiguous → start a fresh research project
                        self._research_project_id = None
                        self._add_notice_row("🔬 Starting a new research project")
            except Exception:
                pass

        self.top_bar.show_status(f"🔬 Deep Research ({depth.capitalize()}): drafting research plan…")

        self._research_activity_log = [
            "🌐 Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            f"⚡ Preset: {depth.capitalize()} Depth",
            "🧠 Drafting research plan and skeptic checklist with local LLM…",
        ]

        init_md = (
            f"### 🔬 Deep Research: Initializing\n\n"
            f"- **🌐 Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **⚡ Preset:** {depth.capitalize()}\n"
            f"- **Status:** Drafting research plan, sub-questions, and skeptic checklist with local LLM…\n\n"
            f"*(Outlining research questions before querying DuckDuckGo)*"
        )

        if not self._current_assistant_widget:
            self._current_assistant_widget = ChatMessageWidget("assistant", init_md)
            self.history_layout.insertWidget(
                self.history_layout.count() - 1, self._current_assistant_widget
            )
        else:
            self._current_assistant_widget.set_content(init_md, is_final=True)

        self._pending_research_topic = question
        self._pending_research_url = url
        self._pending_research_model = model
        self._pending_research_num_ctx = num_ctx

        rw = self._active_research_worker = ResearchWorker(
            url, model, topic=question,
            num_ctx=num_ctx,
            depth=depth,
            project_id=project_id,
            stage=stage,
            auto_approve=auto_approve,
            settings=self._settings,
            parent=self,
        )
        rw.plan_ready.connect(self._on_research_plan_ready)
        rw.progress.connect(self._on_research_progress_detailed)
        rw.status_message.connect(self._on_research_progress)
        rw.research_done.connect(self._on_research_finished)
        rw.error.connect(self._on_worker_error)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._start_research_heartbeat()
        self._scroll_to_bottom(force=True)

    @Slot(object, str)
    def _on_research_plan_ready(self, plan: Any, project_id: str) -> None:
        self._pending_research_plan = plan
        self._pending_research_project_id = project_id
        auto_approve = bool(self._settings.get("auto_approve_research_plans", False))

        self.research_plan_panel.set_plan(plan)

        sq_bullets = "\n".join(
            f"{i}. **{sq.question}**"
            for i, sq in enumerate(plan.sub_questions, 1)
        )
        chk = plan.skeptic_checklist or {}
        myths = chk.get("myths", [])
        myths_str = ", ".join(myths) if myths else "Standard bias & empirical verification"

        if auto_approve:
            self.top_bar.show_status("📋 Research plan approved — starting web search & execution…")
            msg = (
                f"### 📋 Research Plan Drafted & Auto-Approved\n\n"
                f"**Core Question:** {plan.main_question}\n\n"
                f"**Sub-Questions Investigated ({len(plan.sub_questions)}):**\n{sq_bullets}\n\n"
                f"- **Adversarial Skeptic Checklist:** {myths_str}\n\n"
                f"🌐 *Web search and evidence collection in progress…*"
            )
            if self._current_assistant_widget:
                self._current_assistant_widget.set_content(msg, is_final=True)
            self._scroll_to_bottom()
        else:
            self.plan_stack.setCurrentWidget(self.research_plan_panel)
            self.top_bar.show_status("📋 Research plan ready — please review and approve.")

            msg = (
                f"### 📋 Proposed Research Plan\n\n"
                f"**Core Question:** {plan.main_question}\n\n"
                f"**Sub-Questions to Investigate ({len(plan.sub_questions)}):**\n{sq_bullets}\n\n"
                f"- **Adversarial Skeptic Checklist:** {myths_str}\n\n"
                f"Click **🚀 Approve & Start Deep Research Now** below to begin, or open the **📋 Plan** tab to customize sub-questions."
            )
            if self._current_assistant_widget:
                self._current_assistant_widget.set_content(msg, is_final=True)

            btn_box = QWidget()
            btn_layout = QHBoxLayout(btn_box)
            btn_layout.setContentsMargins(0, 4, 0, 4)
            btn_layout.setSpacing(8)

            btn_approve = QPushButton("🚀 Approve & Start Deep Research Now")
            btn_approve.setStyleSheet(
                "QPushButton { background-color: #98c379; color: #1e1e1e; font-weight: bold; font-size: 13px; padding: 8px 18px; border-radius: 5px; }"
                "QPushButton:hover { background-color: #87b368; }"
            )
            btn_edit = QPushButton("✏️ Edit in Plan Tab")
            btn_edit.setStyleSheet(
                "QPushButton { background-color: #2c313a; color: #abb2bf; padding: 8px 14px; border-radius: 5px; border: 1px solid #3e4451; }"
                "QPushButton:hover { background-color: #353b45; color: #dcdcdc; }"
            )

            def _start_approved():
                btn_approve.setEnabled(False)
                btn_edit.setEnabled(False)
                btn_approve.setText("⏳ Deep Research Running…")
                self._on_research_plan_approved(plan)

            btn_approve.clicked.connect(_start_approved)
            btn_edit.clicked.connect(lambda: self.tabs.setCurrentWidget(self.plan_stack))

            btn_layout.addWidget(btn_approve)
            btn_layout.addWidget(btn_edit)
            btn_layout.addStretch()

            self.history_layout.insertWidget(self.history_layout.count() - 1, btn_box)
            self._messages.append({"role": "assistant", "content": msg})
            self._save_current_conversation()
            self._scroll_to_bottom(force=True)

    def _on_research_plan_approved(self, approved_plan: Any) -> None:
        self.tabs.setCurrentIndex(0)
        depth = self.top_bar.research_depth()
        url = getattr(self, "_pending_research_url", "http://localhost:11434")
        model = getattr(self, "_pending_research_model", self.top_bar.current_model())
        num_ctx = getattr(self, "_pending_research_num_ctx", 8192)
        topic = getattr(self, "_pending_research_topic", "")
        pid = getattr(self, "_pending_research_project_id", "")

        self.top_bar.show_status("🔬 Starting deep research execution…")
        self._research_activity_log = [
            "🌐 Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            f"⚡ Depth: {depth.capitalize()}",
            "🚀 Research plan approved — launching investigative engine…",
        ]

        start_md = (
            f"### 🔬 Deep Research: Starting Execution\n\n"
            f"- **🌐 Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **⚡ Depth:** {depth.capitalize()}\n"
            f"- **Status:** Launching investigative engine and DuckDuckGo queries…\n\n"
            f"#### 📋 Live Activity Feed:\n"
            f"- 🌐 Initializing web search queries across {len(approved_plan.sub_questions)} sub-questions\n"
        )
        if not self._current_assistant_widget:
            self._current_assistant_widget = ChatMessageWidget("assistant", start_md)
            self.history_layout.insertWidget(
                self.history_layout.count() - 1, self._current_assistant_widget
            )
        else:
            self._current_assistant_widget.set_content(start_md, is_final=True)

        rw = self._active_research_worker = ResearchWorker(
            url, model, topic=topic,
            num_ctx=num_ctx,
            depth=depth,
            project_id=pid,
            plan=approved_plan,
            stage="execute",
            settings=self._settings,
            parent=self,
        )
        rw.progress.connect(self._on_research_progress_detailed)
        rw.status_message.connect(self._on_research_progress)
        rw.research_done.connect(self._on_research_finished)
        rw.error.connect(self._on_worker_error)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._start_research_heartbeat()
        self._scroll_to_bottom(force=True)

    @Slot(str, int, int, str, str)
    def _on_research_progress_detailed(self, phase: str, step: int, total: int, msg: str, eta: str) -> None:
        self.top_bar.notify_activity()
        self._last_research_progress_time = time.monotonic()
        self.research_panel.update_active_step(phase, step, total, msg)

        # Compute ONE overall percentage across phases
        total_sqs = total if total > 0 else 1
        phase_lower = phase.lower()
        if "plan" in phase_lower:
            pct = int(step * 5 / max(total, 1))
            stage_num = 1
        elif "search" in phase_lower:
            pct = 5 + int(step * 23 / total_sqs)
            stage_num = 2
        elif "read" in phase_lower:
            pct = 28 + int(step * 23 / total_sqs)
            stage_num = 3
        elif "check" in phase_lower or "fact" in phase_lower:
            pct = 51 + int(step * 24 / total_sqs)
            stage_num = 4
        elif "writ" in phase_lower:
            pct = 75 + int(step * 25 / max(total, 1))
            stage_num = 5
        else:
            pct = int(step * 100 / max(total, 1))
            stage_num = 0
        pct = max(0, min(100, pct))

        eta_suffix = f" • {eta}" if eta else ""
        stage_prefix = f"Stage {stage_num} of 5 - {phase}" if stage_num else phase
        self.top_bar.show_status(f"🔬 {stage_prefix} [{step}/{total}]: {msg}{eta_suffix}", pct)

        if not hasattr(self, "_research_activity_log") or self._research_activity_log is None:
            self._research_activity_log = []

        if msg:
            is_dup = any(msg in entry for entry in self._research_activity_log[-30:])
            if not is_dup:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%H:%M:%S")
                self._research_activity_log.append(f"[{ts}] {msg}")

        depth = self.top_bar.research_depth().capitalize()
        recent_logs = self._research_activity_log[-8:]
        log_lines = "\n".join(f"- {line}" for line in recent_logs)

        card_md = (
            f"### 🔬 Deep Research: {stage_prefix} [{step}/{total}]\n\n"
            f"- **🌐 Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **⚡ Depth:** {depth}\n"
            f"- **⏱️ Progress:** Step {step} of {total}{eta_suffix}\n"
            f"- **Current Action:** {msg}\n\n"
            f"#### 📋 Live Activity Feed:\n"
            f"{log_lines}"
        )
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(card_md, is_final=True)
            self._scroll_to_bottom()

    @Slot(str)
    def _on_research_progress(self, msg: str) -> None:
        self.top_bar.notify_activity()
        # Don't overwrite progress bar with indeterminate status if detailed progress arrived recently
        now = time.monotonic()
        last = getattr(self, "_last_research_progress_time", 0)
        if now - last < 2.0:
            pass  # skip show_status to preserve percentage in top bar
        else:
            self.top_bar.show_status(msg)
        if hasattr(self, "_research_activity_log") and self._research_activity_log is not None:
            if msg:
                is_dup = any(msg in entry for entry in self._research_activity_log[-30:])
                if not is_dup:
                    import datetime as _dt
                    ts = _dt.datetime.now().strftime("%H:%M:%S")
                    self._research_activity_log.append(f"[{ts}] {msg}")

    # ── Research Heartbeat Timer ───────────────────────────────────────

    def _start_research_heartbeat(self) -> None:
        """Start a 1-second heartbeat timer showing elapsed time during research."""
        self._research_start_mono = time.monotonic()
        if not hasattr(self, "_research_heartbeat_timer"):
            self._research_heartbeat_timer = QTimer(self)
            self._research_heartbeat_timer.setInterval(1000)
            self._research_heartbeat_timer.timeout.connect(self._on_heartbeat_tick)
        if not self._research_heartbeat_timer.isActive():
            self._research_heartbeat_timer.start()

    def _stop_research_heartbeat(self) -> None:
        """Stop the research heartbeat timer."""
        if hasattr(self, "_research_heartbeat_timer") and self._research_heartbeat_timer.isActive():
            self._research_heartbeat_timer.stop()

    def _on_heartbeat_tick(self) -> None:
        """Update elapsed time in status bar; show 'still working' if stale."""
        if not self._active_research_worker:
            self._stop_research_heartbeat()
            return
        elapsed = time.monotonic() - getattr(self, "_research_start_mono", time.monotonic())
        mm = int(elapsed) // 60
        ss = int(elapsed) % 60
        elapsed_str = f"{mm:02d}:{ss:02d}"

        last_prog = getattr(self, "_last_research_progress_time", 0)
        stale = (time.monotonic() - last_prog) > 5.0 if last_prog else False

        current_status = self.top_bar.lbl_status.text()
        # Only append elapsed / stale suffix, don't replace progress text
        base = current_status.split(" [elapsed")[0].split(" — model")[0]
        suffix = f" [elapsed {elapsed_str}]"
        if stale:
            suffix += " — model is still working…"
        self.top_bar.lbl_status.setText(base + suffix)

    @Slot(str)
    def _on_research_finished(self, project_id: str) -> None:
        """Finalise the research project and present report button in chat."""
        self._stream_flush_timer.stop()
        self._pending_stream_dirty = False
        self._stop_research_heartbeat()
        self.top_bar.set_idle()
        self.research_panel.refresh()

        # Track this as the chat's active research project for continue-or-new routing
        self._research_project_id = project_id
        self._save_current_conversation()

        from core.research_store import get_project
        proj = get_project(project_id) or {}
        title = proj.get("title", "Research Report")
        sources_cnt = proj.get("source_count", 0)

        finish_msg = (
            f"### 🔬 Deep Research Complete: {title}\n\n"
            f"- **🌐 Web Access:** Completed\n"
            f"- **Sources evaluated & verified:** {sources_cnt}\n"
            f"- **Report sections:** 11 sections including Executive Summary, Key Findings, "
            f"Disputed Claims, Mermaid Diagram, Glossary, and Learning Aids.\n"
            f"- **Saved to:** Research Library tab\n"
        )
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(finish_msg, is_final=True)

        # Action button to view report
        btn_open = QPushButton("📖 Open Report in Research Library")
        btn_open.setStyleSheet(
            "QPushButton { background-color: #61afef; color: #1e1e1e; font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #529fd8; }"
        )
        btn_open.clicked.connect(lambda: (self.tabs.setCurrentWidget(self.research_panel), self.research_panel.open_report(project_id)))
        self.history_layout.insertWidget(self.history_layout.count() - 1, btn_open)

        self._messages.append({"role": "assistant", "content": finish_msg})
        self._update_token_meter()
        self.top_bar.hide_status()
        self._save_current_conversation()
        self._active_research_worker = None
        self._cleanup_workers_state()
        self._add_retry_button()

    def _resume_research(self, project_id: str) -> None:
        """Resume an interrupted research project from the Research Library."""
        url = self._settings.get("ollama_url", "http://localhost:11434")
        model = self.top_bar.current_model()
        num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        self.tabs.setCurrentIndex(0)
        self.top_bar.show_status("🔬 Resuming research from checkpoint…")

        self._research_activity_log = [
            "🌐 Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            "🔄 Resuming research project from saved checkpoint…",
        ]

        resume_md = (
            f"### 🔬 Deep Research: Resuming\n\n"
            f"- **🌐 Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **Status:** Rehydrating sources, ledger, and notes from checkpoint…\n"
        )

        if not self._current_assistant_widget:
            self._current_assistant_widget = ChatMessageWidget("assistant", resume_md)
            self.history_layout.insertWidget(
                self.history_layout.count() - 1, self._current_assistant_widget
            )
        else:
            self._current_assistant_widget.set_content(resume_md, is_final=True)

        rw = self._active_research_worker = ResearchWorker(
            url, model, project_id=project_id,
            num_ctx=num_ctx,
            stage="resume",
            settings=self._settings,
            parent=self,
        )
        rw.progress.connect(self._on_research_progress_detailed)
        rw.status_message.connect(self._on_research_progress)
        rw.research_done.connect(self._on_research_finished)
        rw.error.connect(self._on_worker_error)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._start_research_heartbeat()
        self._scroll_to_bottom(force=True)

    def _on_classify_done(self, task_type: str, text: str, url: str, model: str, num_ctx: int) -> None:
        """Handle classify result from background thread."""
        self._classify_worker = None
        self._dispatch_task(task_type, text, url, model, num_ctx)

    def _get_routing_workspace_summary(self) -> dict | None:
        """Return the workspace summary for routing: active workspace or first code attachment."""
        if self._workspace_id:
            ws = workspace.get_workspace(self._workspace_id)
            if ws:
                return ws
        # Check for an attached code project
        for att in self._last_user_attachments:
            if att.get("kind") != "research":
                ws = workspace.get_workspace(att["id"])
                if ws:
                    return ws
        return None

    def _build_existing_context(self, ws: dict) -> str:
        """Build a project summary string for the planner from a workspace."""
        wid = ws.get("id")
        name = ws.get("name", "")
        goal = ws.get("goal", "")
        files = workspace.read_all(wid, max_tokens=2000) if wid else {}
        sigs = build_project_summary(files)
        return (
            f"Project Name: {name}\n"
            f"Goal: {goal}\n"
            f"Existing Files:\n{sigs}"
        )

    def _start_plan_worker(
        self, task_type: str, text: str, url: str, model: str, num_ctx: int, existing_context: str | None
    ) -> None:
        """Launch PlanWorker (used after project routing is resolved)."""
        if task_type == "code":
            w = self._active_plan_worker = PlanWorker(url, model, text, num_ctx, existing_context=existing_context, parent=self)
            w.plan_ready.connect(self._on_plan_ready)
            w.plan_error.connect(self._on_worker_error)
            w.finished.connect(w.deleteLater)
            w.start()
        else:  # both
            pw = self._active_plan_worker = PlanWorker(url, model, text, num_ctx, existing_context=existing_context, parent=self)
            pw.plan_ready.connect(self._on_hybrid_plan_ready)
            pw.plan_error.connect(lambda err: None)
            pw.finished.connect(pw.deleteLater)
            pw.start()

    def _route_and_plan(self, task_type: str, text: str, url: str, model: str, num_ctx: int) -> None:
        """Run project routing (decide_project + LLM fallback) then start planning."""
        ws_summary = self._get_routing_workspace_summary()
        has_attachment = any(a.get("kind") != "research" for a in self._last_user_attachments)

        if not ws_summary:
            self._pending_project_target = {"mode": "new", "workspace_id": None}
            self._start_plan_worker(task_type, text, url, model, num_ctx, None)
            return

        decision = decide_project(text, ws_summary, has_attachment=has_attachment)

        if decision == "ambiguous":
            # Run LLM routing off the GUI thread
            self.top_bar.show_status("🤔 Deciding whether to continue or start a new project…")
            rw = self._route_worker = RouteWorker(url, model, text, ws_summary, num_ctx, parent=self)
            rw.result_ready.connect(
                lambda d: self._on_route_resolved(d, task_type, text, url, model, num_ctx, ws_summary)
            )
            rw.finished.connect(rw.deleteLater)
            rw.start()
        else:
            self._on_route_resolved(decision, task_type, text, url, model, num_ctx, ws_summary)

    def _on_route_resolved(
        self, decision: str, task_type: str, text: str, url: str, model: str, num_ctx: int, ws_summary: dict
    ) -> None:
        """Apply routing decision and launch the planner."""
        self._route_worker = None
        ws_id = ws_summary.get("id")
        existing_context = self._build_existing_context(ws_summary) if decision == "continue" else None
        self._pending_project_target = {"mode": decision, "workspace_id": ws_id}
        self._start_plan_worker(task_type, text, url, model, num_ctx, existing_context)

    def _dispatch_task(self, task_type: str, text: str, url: str, model: str, num_ctx: int) -> None:
        """Route the user request to the correct worker based on task type."""
        self._is_hybrid_mode = (task_type == "both")

        if task_type == "code":
            self.top_bar.show_status("🧠 Architect model is analyzing task…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "🧠 Analyzing request & planning project…")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            else:
                self._current_assistant_widget.set_content("🧠 Analyzing request & planning project…")
            self._route_and_plan("code", text, url, model, num_ctx)
        elif task_type == "both":
            is_low_power = bool(self._settings.get("low_power_mode", False))
            extra_sys = getattr(self, "_last_extra_system", "")
            if is_low_power:
                self.top_bar.show_status("💬 Replying in chat (low-power: planning will run next)…")
                if not self._current_assistant_widget:
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                cw = self._active_chat_worker = ChatWorker(
                    url, model, list(self._messages), {"num_ctx": num_ctx, "low_power_mode": True},
                    self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
                )
                cw.token_received.connect(self._on_token_received)
                cw.compaction_started.connect(self._on_compaction_started)
                cw.compaction_performed.connect(self._on_compaction_performed)
                cw.chat_finished.connect(lambda full, meta: self._start_sequential_plan(url, model, text, num_ctx, full, meta))
                cw.chat_error.connect(self._on_worker_error)
                cw.finished.connect(cw.deleteLater)
                cw.start()
            else:
                self.top_bar.show_status("💬 Explaining in chat & 🧠 planning project…")
                if not self._current_assistant_widget:
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                cw = self._active_chat_worker = ChatWorker(
                    url, model, list(self._messages), {"num_ctx": num_ctx},
                    self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
                )
                cw.token_received.connect(self._on_token_received)
                cw.compaction_started.connect(self._on_compaction_started)
                cw.compaction_performed.connect(self._on_compaction_performed)
                cw.chat_finished.connect(self._on_chat_finished)
                cw.chat_error.connect(self._on_worker_error)
                cw.finished.connect(cw.deleteLater)
                cw.start()

                self._route_and_plan("both", text, url, model, num_ctx)
        else:
            extra_sys = getattr(self, "_last_extra_system", "")
            if not is_compaction_needed(estimate_messages_tokens(self._messages), num_ctx, threshold_ratio=0.75):
                self.top_bar.show_status("💬 Connecting to local model…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            cw = self._active_chat_worker = ChatWorker(
                url, model, list(self._messages), {"num_ctx": num_ctx},
                self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
            )
            cw.token_received.connect(self._on_token_received)
            cw.compaction_started.connect(self._on_compaction_started)
            cw.compaction_performed.connect(self._on_compaction_performed)
            cw.chat_finished.connect(self._on_chat_finished)
            cw.chat_error.connect(self._on_worker_error)
            cw.finished.connect(cw.deleteLater)
            cw.start()

    def _start_sequential_plan(self, url: str, model: str, text: str, num_ctx: int, full_chat: str, meta: dict) -> None:
        """Run planner sequentially after chat reply finishes in low power mode."""
        self._on_chat_finished(full_chat, meta)
        self.top_bar.show_status("🧠 Low-power mode: planning project…")
        self._route_and_plan("both", text, url, model, num_ctx)

    @Slot(object)
    def _on_hybrid_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        if getattr(self, "_is_hybrid_mode", False) and self._current_assistant_widget:
            footer = (
                f"\n\n---\n### 📋 Project Plan Ready: {plan.goal} ({len(plan.steps)} steps)\n\n"
                + "\n".join(f"- **Step {s.step}**: {s.description} → `{s.file}`" for s in plan.steps)
                + "\n\n👉 A complete project plan has been prepared in the **📋 Plan** tab! Switch to the Plan tab and click **Approve & Start Coding** whenever you're ready to build."
            )
            curr = self._current_assistant_widget.get_content()
            if "📋 Project Plan Ready" not in curr:
                updated = curr + footer
                self._current_assistant_widget.set_content(updated, is_final=True)
                if self._messages and self._messages[-1].get("role") == "assistant":
                    self._messages[-1]["content"] = updated
            self._is_hybrid_mode = False
        self.top_bar.show_status(f"💬 Answer ready & 📋 Project plan prepared ({len(plan.steps)} steps) in Plan tab", 100)
        self._save_current_conversation()

    @Slot(object)
    def _on_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        self.plan_stack.setCurrentWidget(self.plan_panel)
        self.tabs.setCurrentIndex(1)
        self.top_bar.show_status(f"📋 Plan ready ({len(plan.steps)} steps) — review in Plan tab", 100)
        plan_msg = (
            f"📋 **Plan Ready: {plan.goal}** ({len(plan.steps)} steps)\n\n"
            + "\n".join(f"  {s.step}. {s.description} → `{s.file}`" for s in plan.steps)
            + "\n\n👉 Review the plan in the **📋 Plan** tab and click **Approve & Start Coding** to begin building."
        )
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(plan_msg)
        self._messages.append({"role": "assistant", "content": plan_msg})
        self._save_current_conversation()
        self._cleanup_workers_state()
        self._add_retry_button()

        # Request AI title after plan finishes (avoids model reload race)
        if not self._current_chat_has_ai_title and self._last_user_prompt:
            url = self._settings.get("ollama_url", "http://localhost:11434")
            model = self.top_bar.current_model()
            num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
            self._request_ai_chat_title(self._current_chat_id, self._last_user_prompt, model, url, num_ctx)

    @Slot(object)
    def _on_plan_approved(self, plan: CodingPlan) -> None:
        if self._build_running:
            return

        self.top_bar.set_working()
        self._build_running = True
        self._building_chat_id = self._current_chat_id
        self._building_plan = plan

        # Resolve project target (default to new if no routing happened yet)
        target = getattr(self, "_pending_project_target", None) or {"mode": "new", "workspace_id": None}
        mode = target.get("mode", "new")
        old_workspace_id = self._workspace_id

        if mode == "new":
            # Save previous workspace if any
            if old_workspace_id:
                old_ws = workspace.get_workspace(old_workspace_id)
                old_name = old_ws.get("name", old_workspace_id) if old_ws else old_workspace_id
                try:
                    workspace._update_index_entry(old_workspace_id, status="saved")
                except Exception:
                    pass
                self._add_notice_row(f"💾 Previous build saved as **{old_name}**")

            # Create a fresh workspace
            name = plan.project_name or workspace.slugify(plan.goal)
            ws = workspace.create_workspace(name, goal=plan.goal, kind="code", chat_id=self._current_chat_id)
            self._workspace_id = ws.get("id")
            if self._workspace_id and self._workspace_id not in self._workspace_ids_history:
                self._workspace_ids_history.append(self._workspace_id)
            # Fresh build: start from scratch
            self._build_files = {}
            self._active_plan = None
            self.artifacts_panel.clear()
            self._add_notice_row(f"🚀 Started a new project **{name}**")
        else:  # continue
            ws_id = target.get("workspace_id") or self._workspace_id
            self._workspace_id = ws_id
            if ws_id:
                ws = workspace.get_workspace(ws_id)
                ws_name = ws.get("name", ws_id) if ws else ws_id
                # Load existing workspace files into build files for continuation
                existing_files = workspace.read_all(ws_id) if ws_id else {}
                self._build_files = dict(existing_files)
                self.artifacts_panel.clear()
                for fn, content in existing_files.items():
                    self.artifacts_panel.add_file(fn, content)
                self._add_notice_row(f"📂 Continuing in project **{ws_name}**")

        self._building_files = dict(self._build_files)

        self.plan_panel.btn_approve.setText("Building in Progress…")
        self.plan_panel.btn_approve.setEnabled(False)
        self.tabs.setCurrentIndex(2)
        self.btn_stop.setEnabled(True)

        # Resume step: only when the SAME plan already has completed steps
        same_plan_has_completed = bool(self._active_plan and self._active_plan is plan and any(s.completed for s in plan.steps))
        start = get_resume_step(plan) if same_plan_has_completed else 1
        existing = dict(self._building_files)
        total = len(plan.steps)
        label = plan.steps[start - 1].file if start <= total else "?"
        action = "Resuming" if start > 1 else "Starting"
        self.top_bar.show_status(f"⚙️ {action} [{start}/{total}] {label}…", 0)

        self.sidebar.show_wip(self._building_chat_id)
        save_session(self._building_chat_id, plan, self._building_files, start, workspace_id=self._workspace_id)
        self._save_current_conversation()

        is_lp = bool(self._settings.get("low_power_mode", False))
        max_agents = 1 if is_lp else int(self._settings.get("max_agents", 1))
        auto_test = bool(self._settings.get("auto_test", True))

        w = self._active_code_worker = CodeWorker(
            self._settings.get("ollama_url", "http://localhost:11434"),
            self.top_bar.current_model(),
            plan,
            getattr(self, "_active_num_ctx", 8192),
            int(self._settings.get("max_continuations", 8)),
            max_agents=max_agents,
            auto_test=auto_test,
            start_step=start,
            existing_files=existing,
            low_power=is_lp,
            workspace_id=self._workspace_id,
            parent=self,
        )
        w.step_started.connect(lambda s, f: self._on_step_started(s, f, total))
        w.part_started.connect(lambda f, p, m: self._on_part_started(f, p, m))
        w.test_progress.connect(lambda f, msg: self._on_test_progress(f, msg))
        w.file_completed.connect(self._on_file_checkpoint)
        w.all_completed.connect(self._on_all_files_completed)
        w.worker_error.connect(self._on_build_error)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_step_started(self, step_num: int, filename: str, total: int) -> None:
        self.top_bar.notify_activity()
        if self._current_chat_id == self._building_chat_id:
            pct = int(((step_num - 1) / total) * 100) if total else 0
            self.top_bar.show_status(f"⚙️ [{step_num}/{total}] Writing {filename}…", pct)

    def _on_part_started(self, filename: str, part: int, max_parts: int) -> None:
        self.top_bar.notify_activity()
        if self._current_chat_id == self._building_chat_id:
            self.artifacts_panel.show_continuation_progress(filename, part, max_parts)
            self.top_bar.show_status(f"⚡ Continuing {filename} ({part}/{max_parts})…")

    def _on_test_progress(self, filename: str, msg: str) -> None:
        self.top_bar.notify_activity()
        if self._current_chat_id == self._building_chat_id:
            self.top_bar.show_status(msg)
            self.artifacts_panel.show_test_status(filename, msg)

    @Slot(str, str, int)
    def _on_file_checkpoint(self, filename: str, code: str, step_num: int) -> None:
        self.top_bar.notify_activity()
        target_id = self._building_chat_id or self._current_chat_id
        self._building_files[filename] = code

        # Persist session (plan state + next step + workspace_id) - NO full chat reload per file
        if self._building_plan:
            for s in self._building_plan.steps:
                if s.step == step_num or s.file == filename:
                    s.completed = True
            save_session(
                target_id, self._building_plan, self._building_files, step_num + 1,
                workspace_id=self._workspace_id,
            )

        # Update UI if user is actively viewing the building chat
        if self._current_chat_id == target_id:
            self._build_files[filename] = code
            self.artifacts_panel.add_file(filename, code)
            self.plan_panel.mark_step_completed(step_num)
            total = len(self._building_plan.steps) if self._building_plan else 1
            pct = int((step_num / total) * 100) if total else 0
            self.top_bar.show_status(f"✅ {filename} ({step_num}/{total})", pct)

    @Slot(dict)
    def _on_all_files_completed(self, files: dict[str, str]) -> None:
        self.top_bar.set_idle()
        target_id = self._building_chat_id or self._current_chat_id
        self._build_running = False

        complete_session(target_id)
        self.sidebar.hide_wip()

        # Prefer files from the workspace on disk (source of truth)
        ws_files = workspace.read_all(self._workspace_id) if self._workspace_id else files
        summary = generate_project_walkthrough(self._building_plan or self._active_plan, ws_files)

        # Include project folder path in the summary
        if self._workspace_id:
            wp = workspace.workspace_path(self._workspace_id)
            if wp:
                summary = f"{summary}\n\n📁 **Project folder:** `{wp}`"

        c_data = load_chat(target_id)
        if c_data:
            c_data.setdefault("messages", []).append({"role": "assistant", "content": summary})
            # Stop storing file contents in chat JSON; store workspace_id instead
            c_data["artifacts"] = {}
            c_data["workspace_id"] = self._workspace_id
            save_chat(c_data)

        if self._current_chat_id == target_id:
            self._messages.append({"role": "assistant", "content": summary})
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("assistant", summary))
            self.top_bar.show_status(f"✅ All {len(ws_files)} files completed! Walkthrough generated.", 100)
            QTimer.singleShot(4000, self.top_bar.hide_status)
            self.plan_panel.btn_approve.setText("✅ Build Completed")
            self.plan_panel.btn_approve.setEnabled(False)
            self._add_retry_button()

        self._building_chat_id = None
        self._building_plan = None
        self._building_files = {}
        self._active_code_worker = None
        self.btn_stop.setEnabled(False)

    @Slot(str)
    def _on_build_error(self, err_msg: str) -> None:
        self.top_bar.set_idle()
        target_id = self._building_chat_id or self._current_chat_id
        self._build_running = False

        err = f"⚠️ Build error: {err_msg}\nProgress saved — click **Resume Building** in Plan tab."
        c_data = load_chat(target_id)
        if c_data:
            c_data.setdefault("messages", []).append({"role": "assistant", "content": err})
            save_chat(c_data)

        if self._building_plan:
            save_session(target_id, self._building_plan, self._building_files, get_resume_step(self._building_plan))
            self.sidebar.show_wip(target_id)

        if self._current_chat_id == target_id:
            self._messages.append({"role": "assistant", "content": err})
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("assistant", err))
            self.top_bar.hide_status()
            if self._active_plan:
                self.plan_panel.btn_approve.setText("▶ Resume Building")
                self.plan_panel.btn_approve.setEnabled(True)
            self._add_retry_button()

        self._building_chat_id = None
        self._building_plan = None
        self._building_files = {}
        self._active_code_worker = None
        self.btn_stop.setEnabled(False)

    @Slot(str)
    def _on_token_received(self, token: str) -> None:
        self.top_bar.notify_activity()
        self._current_assistant_text.append(token)
        self._pending_stream_dirty = True
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _flush_stream_ui(self) -> None:
        """Render batched tokens smoothly at ~40fps without UI stutter."""
        if not self._pending_stream_dirty:
            return
        self._pending_stream_dirty = False
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content("".join(self._current_assistant_text), is_final=False)
        self.top_bar.show_status(f"💬 Generating ({len(self._current_assistant_text)} tokens)…")
        # Use debounced scroll instead of direct call to avoid layout thrashing
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    @Slot(int, int)
    def _on_compaction_started(self, current_tok: int, threshold_tok: int) -> None:
        """Show compaction in the status bar and progress bar."""
        self.top_bar.show_status(
            f"⚡ Compacting conversation ({current_tok:,} tokens exceeded 75% limit)…",
            40,
        )

    def _add_notice_row(self, markdown_text: str) -> None:
        """Insert a small notice row into the chat history (not saved to messages)."""
        try:
            from ui.message_widget import ChatMessageWidget

            notice = ChatMessageWidget("assistant", markdown_text)
            self.history_layout.insertWidget(self.history_layout.count() - 1, notice)
        except Exception:
            notice = QLabel(markdown_text)
            notice.setStyleSheet("color: #abb2bf; font-size: 11px; font-style: italic; padding: 2px 0;")
            self.history_layout.insertWidget(self.history_layout.count() - 1, notice)

    @Slot(list, int, int)
    def _on_compaction_performed(self, new_messages: list, old_tok: int, new_tok: int) -> None:
        """Apply compacted messages to chat history so future requests don't loop compaction!"""
        self._messages = list(new_messages)
        self._save_current_conversation()
        self._update_token_meter()

        notice = QLabel(f"⚡ Compacted conversation (~{old_tok:,} → ~{new_tok:,} tokens)")
        notice.setStyleSheet("color: #e5c07b; font-size: 11px; font-style: italic; padding: 2px 0;")
        self.history_layout.insertWidget(self.history_layout.count() - 2, notice)

        self.top_bar.show_status(
            f"✅ Compacted to {new_tok:,} tokens! Connecting to model…",
            75,
        )

    @Slot(str, dict)
    def _on_chat_finished(self, full_content: str, meta: dict) -> None:
        self._stream_flush_timer.stop()
        self._pending_stream_dirty = False
        self.top_bar.set_idle()
        if self._active_plan and getattr(self, "_is_hybrid_mode", False):
            footer = (
                f"\n\n---\n### 📋 Project Plan Ready: {self._active_plan.goal} ({len(self._active_plan.steps)} steps)\n\n"
                + "\n".join(f"- **Step {s.step}**: {s.description} → `{s.file}`" for s in self._active_plan.steps)
                + "\n\n👉 A complete project plan has been prepared in the **📋 Plan** tab! Switch to the Plan tab and click **Approve & Start Coding** whenever you're ready to build."
            )
            full_content += footer
            self._is_hybrid_mode = False

        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(full_content, is_final=True)

        self._messages.append({"role": "assistant", "content": full_content})
        p, e = meta.get("prompt_eval_count", 0), meta.get("eval_count", 0)
        self._last_prompt_eval_tokens = (p + e) if p else 0
        self._update_token_meter()
        self.top_bar.hide_status()
        self._save_current_conversation()
        self._cleanup_workers_state()
        self._add_retry_button()

        # Request AI title after chat finishes (avoids model reload race)
        if not self._current_chat_has_ai_title and self._last_user_prompt:
            url = self._settings.get("ollama_url", "http://localhost:11434")
            model = self.top_bar.current_model()
            num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
            self._request_ai_chat_title(self._current_chat_id, self._last_user_prompt, model, url, num_ctx)

    @Slot(str)
    def _on_worker_error(self, err_msg: str) -> None:
        self._stop_research_heartbeat()
        self.top_bar.set_idle()
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(f"⚠️ {err_msg}")
        self.top_bar.hide_status()
        self._cleanup_workers_state()
        self._add_retry_button()

    def _stop_chat_generation(self) -> None:
        """Stop chat and planning workers without interrupting background code builds."""
        self._stop_research_heartbeat()
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
            self._flush_stream_ui()
        for w in (self._active_chat_worker, self._active_plan_worker,
                  self._classify_worker, self._active_research_worker):
            if w and w.isRunning():
                if hasattr(w, "stop"):
                    w.stop()
        self._active_title_workers.clear()
        self._active_chat_worker = None
        self._active_plan_worker = None
        self._classify_worker = None
        self._active_research_worker = None
        self._current_assistant_widget = None

    def _stop_generation(self) -> None:
        """Handle explicit Stop button click."""
        self._stop_chat_generation()

        if self._active_code_worker and self._active_code_worker.isRunning():
            self._active_code_worker.stop()
            self._active_code_worker.wait(500)
            self._active_code_worker = None

        if self._build_running:
            self._build_running = False
            self.top_bar.set_idle()
            target_id = self._building_chat_id or self._current_chat_id
            if self._building_plan:
                save_session(target_id, self._building_plan, self._building_files, get_resume_step(self._building_plan))
                self.sidebar.show_wip(target_id)
            if self._current_chat_id == target_id:
                self.plan_panel.btn_approve.setText("▶ Resume Building")
                self.plan_panel.btn_approve.setEnabled(True)

        self.top_bar.hide_status()
        self._cleanup_workers_state()
        self._add_retry_button()

    def _cleanup_workers_state(self) -> None:
        self.btn_send.setEnabled(True)
        if not self._build_running and not self._active_chat_worker:
            self.btn_stop.setEnabled(False)
        self._active_chat_worker = None
        self._active_plan_worker = None
        self._classify_worker = None
        self._active_research_worker = None
        self._current_assistant_widget = None

    def _save_current_conversation(self) -> None:
        # Never save empty conversations — prevents ghost chat creation
        if not self._messages:
            return

        title = self._current_chat_title or next(
            (m["content"][:30] + ("…" if len(m["content"]) > 30 else "") for m in self._messages if m["role"] == "user"),
            "New Chat",
        )
        chat_data: dict[str, Any] = {
            "id": self._current_chat_id,
            "title": title,
            "ai_title": self._current_chat_has_ai_title,
            "messages": self._messages,
            "workspace_id": self._workspace_id,
            "research_project_id": self._research_project_id,
        }
        # Stop storing file contents in chat JSON for builds that have a workspace
        if not self._workspace_id:
            chat_data["artifacts"] = dict(self._build_files)
        if self._active_plan:
            chat_data["plan"] = {
                "goal": self._active_plan.goal,
                "assumptions": self._active_plan.assumptions,
                "files": self._active_plan.files,
                "project_name": getattr(self._active_plan, "project_name", ""),
                "steps": [
                    {
                        "step": s.step,
                        "description": s.description,
                        "file": s.file,
                        "completed": s.completed,
                        "action": getattr(s, "action", "create"),
                    }
                    for s in self._active_plan.steps
                ],
                "raw_text": self._active_plan.raw_text,
            }
        save_chat(chat_data)
        self.sidebar.upsert_chat(self._current_chat_id, title)

    def _add_load_earlier_button(self) -> None:
        """Add a 'Load earlier messages' button at the top of the chat history."""
        self._remove_load_earlier_button()
        btn = QPushButton("⬆ Load earlier messages")
        btn.setObjectName("load_earlier_btn")
        btn.setStyleSheet(
            "QPushButton { font-size: 12px; padding: 6px 16px; background: #282c34; "
            "color: #61afef; border: 1px solid #3e4451; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #3e4451; color: #ffffff; border-color: #61afef; }"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._on_load_earlier_clicked)
        # Insert at position 0 (before the stretch and all messages)
        self.history_layout.insertWidget(0, btn)

    def _remove_load_earlier_button(self) -> None:
        """Remove existing 'Load earlier' button if present."""
        for i in range(self.history_layout.count()):
            item = self.history_layout.itemAt(i)
            if item and item.widget() and item.widget().objectName() == "load_earlier_btn":
                w = self.history_layout.takeAt(i).widget()
                if w:
                    w.deleteLater()
                return

    def _on_load_earlier_clicked(self) -> None:
        """Load 40 more earlier messages into the chat history."""
        offset = getattr(self, "_lazy_load_offset", 0)
        if offset <= 0:
            self._remove_load_earlier_button()
            return
        new_offset = max(0, offset - 40)
        batch = self._messages[new_offset:offset]
        self._lazy_load_offset = new_offset

        # Remove the old button
        self._remove_load_earlier_button()

        # Insert messages at the top (after position 0, before existing messages)
        insert_pos = 0
        for msg in batch:
            self.history_layout.insertWidget(insert_pos, ChatMessageWidget(msg["role"], msg["content"]))
            insert_pos += 1

        # Re-add button if there are still more messages
        if new_offset > 0:
            self._add_load_earlier_button()

    def _request_ai_chat_title(self, chat_id: str, prompt: str, model: str, url: str, num_ctx: int) -> None:
        """Spawn background TitleWorker to summarize conversation topic."""
        title_model = self._settings.get("summarizer_model") or model
        if not title_model or not url:
            return
        tw = TitleWorker(chat_id, url, title_model, prompt, num_ctx=num_ctx, parent=self)
        tw.title_ready.connect(self._on_ai_title_ready)
        tw.finished.connect(lambda: self._active_title_workers.remove(tw) if tw in self._active_title_workers else None)
        tw.finished.connect(tw.deleteLater)
        self._active_title_workers.append(tw)
        tw.start()

    @Slot(str, str)
    def _on_ai_title_ready(self, chat_id: str, title: str) -> None:
        """Apply AI-generated conversation title to sidebar, memory, and disk."""
        if not title:
            return
        # 1. Update sidebar item
        self.sidebar.upsert_chat(chat_id, title)

        # 2. Update memory if current chat
        if self._current_chat_id == chat_id:
            self._current_chat_title = title
            self._current_chat_has_ai_title = True

        # 3. Update saved file on disk
        if c_data := load_chat(chat_id):
            c_data["title"] = title
            c_data["ai_title"] = True
            save_chat(c_data)

    def _on_scroll_value_changed(self, value: int) -> None:
        """Track if the user has manually scrolled up to read earlier text."""
        if sb := self.scroll_area.verticalScrollBar():
            # If user is more than 80px above bottom, don't hijack scroll position
            if sb.maximum() - value > 80:
                self._user_scrolled_away = True
            else:
                self._user_scrolled_away = False

    def _scroll_to_bottom(self, force: bool = False) -> None:
        """Request a debounced scroll-to-bottom. If force=True, overrides user scroll position."""
        if force:
            self._user_scrolled_away = False
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _do_scroll_to_bottom(self) -> None:
        """Actual scroll execution (called by debounce timer). Only scrolls if user hasn't scrolled up."""
        if self._user_scrolled_away:
            return
        if sb := self.scroll_area.verticalScrollBar():
            sb.setValue(sb.maximum())

    def _open_settings(self) -> None:
        if SettingsDialog(self._settings, self._models, parent=self).exec():
            self._on_model_changed(self.top_bar.current_model())
            # Re-apply internet access state after settings change
            self.top_bar.set_internet_access(bool(self._settings.get("internet_access", False)))

    def stop_workers(self) -> None:
        self._stop_generation()
