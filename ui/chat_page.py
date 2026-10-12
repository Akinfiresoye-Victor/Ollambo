"""
Main Chat Page — ChatGPT style interface with streaming, context compaction,
Plan-First Coding Mode, Long Code Continuation, Multi-Agent Generation,
Background Builds with Persistent Sessions, and Keep-Alive Model Connection.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.build_session import complete_session, get_resume_step, load_session, reconstruct_plan, save_session
from core.context_manager import estimate_messages_tokens, is_compaction_needed
from core.ollama_client import OllamaClient
from core.planner import CodingPlan
from core.storage import list_active_sessions, load_chat, save_chat
from core.task_classifier import classify_task, classify_heuristic
from core.walkthrough import generate_project_walkthrough
from ui.artifacts_panel import ArtifactsPanel
from ui.chat_sidebar import ChatSidebar
from ui.chat_top_bar import ChatTopBar
from ui.message_widget import ChatMessageWidget
from ui.plan_panel import PlanPanel
from ui.settings_dialog import SettingsDialog
from workers.chat_worker import ChatWorker
from workers.code_worker import CodeWorker
from workers.models_worker import ModelsWorker
from workers.plan_worker import PlanWorker


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

    def __init__(self, ollama_url: str, model: str, prompt: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._prompt = prompt

    def run(self) -> None:
        result = classify_task(self._prompt, self._url, self._model)
        self.result_ready.emit(result)


class PreloadWorker(QThread):
    """Background thread to preload/warm a model into RAM/VRAM without freezing UI."""

    finished_preload = Signal(str, bool)

    def __init__(self, ollama_url: str, model: str, parent=None) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model

    def run(self) -> None:
        client = OllamaClient(self._url)
        ok = client.preload_model(self._model, keep_alive="5m")
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
        self._messages: list[dict] = []
        self._last_prompt_eval_tokens = 0

        self._active_chat_worker: ChatWorker | None = None
        self._active_plan_worker: PlanWorker | None = None
        self._active_code_worker: CodeWorker | None = None
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

        self._is_hybrid_mode = False

        # Smooth ~40fps streaming UI flush timer to prevent event loop bottlenecks
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(25)
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

        self.artifacts_panel = ArtifactsPanel()

        self.tabs.addTab(self.scroll_area, "💬 Chat")
        self.tabs.addTab(self.plan_panel, "📋 Plan")
        self.tabs.addTab(self.artifacts_panel, "📦 Artifacts")
        right_layout.addWidget(self.tabs)

        # Bottom Input Area
        input_row = QHBoxLayout()
        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("Type a message or coding request…")
        self.input_edit.setMaximumHeight(80)
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
        self._active_num_ctx = min(int(self._settings.get("num_ctx", 8192)), ctx)
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
        w.start()

    # ── Keep-Alive & Model Warming ─────────────────────────────────────

    def _on_keep_warm_requested(self, model: str) -> None:
        url = self._settings.get("ollama_url", "http://localhost:11434")
        pw = self._preload_worker = PreloadWorker(url, model, parent=self)
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

    # ── Chat & Tab Navigation ──────────────────────────────────────────

    def _start_new_chat(self) -> None:
        # Only save if there are actual messages to save (prevents ghost chats)
        if self._messages:
            self._save_current_conversation()

        # Stop any active chat/plan workers, but leave CodeWorker running in background!
        self._stop_chat_generation()

        self._current_chat_id = str(uuid.uuid4())
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

    def _on_chat_deleted(self, chat_id: str) -> None:
        """Handle a chat being deleted from the sidebar."""
        if chat_id == self._current_chat_id:
            # The active chat was deleted — reset to a fresh state without saving
            self._stop_chat_generation()
            self._current_chat_id = str(uuid.uuid4())
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
        self._messages = data.get("messages", [])
        self._last_prompt_eval_tokens = 0
        self._clear_history_ui()
        for msg in self._messages:
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
            self._build_files = dict(data.get("artifacts", {}))
            for fname, code in self._build_files.items():
                self.artifacts_panel.add_file(fname, code)

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
        self._build_files = dict(session.get("completed_files", {}))
        self.artifacts_panel.clear()
        for fname, code in self._build_files.items():
            self.artifacts_panel.add_file(fname, code)
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

        self._send_message()

    # ── Sending & Processing Messages ──────────────────────────────────

    def _send_message(self) -> None:
        text = self.input_edit.toPlainText().strip()
        model = self.top_bar.current_model()
        if not text or not model:
            return

        self.input_edit.clear()
        self._last_user_prompt = text

        # Remove any existing retry button before adding new messages
        self._remove_retry_button()

        self._messages.append({"role": "user", "content": text})
        self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("user", text))
        self._save_current_conversation()

        self.top_bar.set_working()
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)

        url = self._settings.get("ollama_url", "http://localhost:11434")
        num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        self._current_assistant_text = []

        est_tokens = estimate_messages_tokens(self._messages)
        if is_compaction_needed(est_tokens, num_ctx, threshold_ratio=0.75):
            self.top_bar.show_status(f"⚡ Checking tokens ({est_tokens:,}/{num_ctx:,}) — compacting history…", 30)

        mode = self.top_bar.current_mode()
        if mode == "code":
            self._dispatch_task("code", text, url, model, num_ctx)
        elif mode == "chat":
            self._dispatch_task("chat", text, url, model, num_ctx)
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
                cw = self._classify_worker = ClassifyWorker(url, model, text, parent=self)
                cw.result_ready.connect(lambda task_type: self._on_classify_done(task_type, text, url, model, num_ctx))
                cw.start()

        self._scroll_to_bottom(force=True)

    def _on_classify_done(self, task_type: str, text: str, url: str, model: str, num_ctx: int) -> None:
        """Handle classify result from background thread."""
        self._classify_worker = None
        self._dispatch_task(task_type, text, url, model, num_ctx)

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
            w = self._active_plan_worker = PlanWorker(url, model, text, num_ctx, parent=self)
            w.plan_ready.connect(self._on_plan_ready)
            w.plan_error.connect(self._on_worker_error)
            w.start()
        elif task_type == "both":
            self.top_bar.show_status("💬 Explaining in chat & 🧠 planning project…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            cw = self._active_chat_worker = ChatWorker(
                url, model, list(self._messages), {"num_ctx": num_ctx},
                self._settings.get("summarizer_model", ""), parent=self,
            )
            cw.token_received.connect(self._on_token_received)
            cw.compaction_started.connect(self._on_compaction_started)
            cw.compaction_performed.connect(self._on_compaction_performed)
            cw.chat_finished.connect(self._on_chat_finished)
            cw.chat_error.connect(self._on_worker_error)
            cw.start()

            pw = self._active_plan_worker = PlanWorker(url, model, text, num_ctx, parent=self)
            pw.plan_ready.connect(self._on_hybrid_plan_ready)
            pw.plan_error.connect(lambda err: None)
            pw.start()
        else:
            if not is_compaction_needed(estimate_messages_tokens(self._messages), num_ctx, threshold_ratio=0.75):
                self.top_bar.show_status("💬 Connecting to local model…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            cw = self._active_chat_worker = ChatWorker(
                url, model, list(self._messages), {"num_ctx": num_ctx},
                self._settings.get("summarizer_model", ""), parent=self,
            )
            cw.token_received.connect(self._on_token_received)
            cw.compaction_started.connect(self._on_compaction_started)
            cw.compaction_performed.connect(self._on_compaction_performed)
            cw.chat_finished.connect(self._on_chat_finished)
            cw.chat_error.connect(self._on_worker_error)
            cw.start()

    @Slot(object)
    def _on_hybrid_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        self.top_bar.show_status(f"💬 Answer ready & 📋 Project plan prepared ({len(plan.steps)} steps) in Plan tab", 100)
        self._save_current_conversation()

    @Slot(object)
    def _on_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
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

    @Slot(object)
    def _on_plan_approved(self, plan: CodingPlan) -> None:
        if self._build_running:
            return

        self.top_bar.set_working()
        self._build_running = True
        self._building_chat_id = self._current_chat_id
        self._building_plan = plan
        self._building_files = dict(self._build_files)

        self.plan_panel.btn_approve.setText("Building in Progress…")
        self.plan_panel.btn_approve.setEnabled(False)
        self.tabs.setCurrentIndex(2)
        self.btn_stop.setEnabled(True)

        start = get_resume_step(plan) if self._building_files else 1
        existing = dict(self._building_files)
        total = len(plan.steps)
        label = plan.steps[start - 1].file if start <= total else "?"
        action = "Resuming" if start > 1 else "Starting"
        self.top_bar.show_status(f"⚙️ {action} [{start}/{total}] {label}…", 0)

        self.sidebar.show_wip(self._building_chat_id)
        save_session(self._building_chat_id, plan, self._building_files, start)
        self._save_current_conversation()

        max_agents = int(self._settings.get("max_agents", 2))
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
            parent=self,
        )
        w.step_started.connect(lambda s, f: self._on_step_started(s, f, total))
        w.part_started.connect(lambda f, p, m: self._on_part_started(f, p, m))
        w.test_progress.connect(lambda f, msg: self._on_test_progress(f, msg))
        w.file_completed.connect(self._on_file_checkpoint)
        w.all_completed.connect(self._on_all_files_completed)
        w.worker_error.connect(self._on_build_error)
        w.start()

    def _on_step_started(self, step_num: int, filename: str, total: int) -> None:
        self.top_bar.notify_activity()
        if self._current_chat_id == self._building_chat_id:
            self.plan_panel.mark_step_completed(step_num)
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

        # Always persist session and chat file on disk for the building chat
        if self._building_plan:
            for s in self._building_plan.steps:
                if s.step == step_num or s.file == filename:
                    s.completed = True
            save_session(target_id, self._building_plan, self._building_files, step_num + 1)

        c_data = load_chat(target_id)
        if c_data:
            c_data["artifacts"] = dict(self._building_files)
            if self._building_plan:
                c_data["plan"] = {
                    "goal": self._building_plan.goal,
                    "assumptions": self._building_plan.assumptions,
                    "files": self._building_plan.files,
                    "steps": [
                        {"step": s.step, "description": s.description, "file": s.file, "completed": s.completed}
                        for s in self._building_plan.steps
                    ],
                    "raw_text": self._building_plan.raw_text,
                }
            save_chat(c_data)

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

        # Generate detailed project summary and step-by-step walkthrough on how to run it
        summary = generate_project_walkthrough(self._building_plan or self._active_plan, files)

        c_data = load_chat(target_id)
        if c_data:
            c_data.setdefault("messages", []).append({"role": "assistant", "content": summary})
            c_data["artifacts"] = dict(files)
            save_chat(c_data)

        if self._current_chat_id == target_id:
            self._messages.append({"role": "assistant", "content": summary})
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("assistant", summary))
            self.top_bar.show_status(f"✅ All {len(files)} files completed! Walkthrough generated.", 100)
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

    @Slot(str)
    def _on_worker_error(self, err_msg: str) -> None:
        self.top_bar.set_idle()
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(f"⚠️ {err_msg}")
        self.top_bar.hide_status()
        self._cleanup_workers_state()
        self._add_retry_button()

    def _stop_chat_generation(self) -> None:
        """Stop chat and planning workers without interrupting background code builds."""
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()
            self._flush_stream_ui()
        for w in (self._active_chat_worker, self._active_plan_worker, self._classify_worker):
            if w and w.isRunning():
                if hasattr(w, "stop"):
                    w.stop()
                w.wait(400)
        self._active_chat_worker = None
        self._active_plan_worker = None
        self._classify_worker = None
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
        self._current_assistant_widget = None

    def _save_current_conversation(self) -> None:
        # Never save empty conversations — prevents ghost chat creation
        if not self._messages:
            return

        title = next(
            (m["content"][:30] + ("…" if len(m["content"]) > 30 else "") for m in self._messages if m["role"] == "user"),
            "New Chat",
        )
        chat_data: dict[str, Any] = {
            "id": self._current_chat_id,
            "title": title,
            "messages": self._messages,
            "artifacts": dict(self._build_files),
        }
        if self._active_plan:
            chat_data["plan"] = {
                "goal": self._active_plan.goal,
                "assumptions": self._active_plan.assumptions,
                "files": self._active_plan.files,
                "steps": [
                    {"step": s.step, "description": s.description, "file": s.file, "completed": s.completed}
                    for s in self._active_plan.steps
                ],
                "raw_text": self._active_plan.raw_text,
            }
        save_chat(chat_data)
        self.sidebar.upsert_chat(self._current_chat_id, title)

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

    def stop_workers(self) -> None:
        self._stop_generation()
