"""
Main Chat Page — ChatGPT style interface with streaming, context compaction,
Plan-First Coding Mode, Long Code Continuation, Multi-Agent Generation,
Background Builds with Persistent Sessions, and Keep-Alive Model Connection.
"""

from __future__ import annotations

import copy
from functools import partial
import time
import uuid
from typing import Any

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
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
from core.project_router import classify_with_attachment, decide_project
from core.workspace_context import build_attachment_context
from core.build_session import complete_session, get_resume_step, load_session, reconstruct_plan, save_session
from core.context_manager import estimate_messages_tokens, is_compaction_needed
from core.planner import CodingPlan
from core.storage import (
    get_effective_keep_alive,
    get_effective_keep_alive_seconds,
    load_chat,
    resolve_num_ctx,
    save_chat,
)
from core.task_classifier import classify_heuristic
from core.walkthrough import generate_project_walkthrough
from core.chat_session import ChatSession
from ui.artifacts_panel import ArtifactsPanel
from ui.widgets.artifact_card import ArtifactCard
from ui.artifact_list import ArtifactListPanel
from ui.artifact_viewer import ArtifactViewer
from ui.background_workers import (
    ClassifyWorker,
    ContextLengthWorker,
    PreloadWorker,
    RouteWorker,
    UnloadWorker,
)
from ui.chat_sidebar import ChatSidebar
from ui.chat_top_bar import ChatTopBar
from ui.chat_view import ChatView
from ui.icons import icon
from ui.message_widget import ChatMessageWidget
from ui.navigation import Navigator
from ui.plan_panel import PlanPanel
from ui.research_panel import ResearchPanel
from ui.research_plan_panel import ResearchPlanPanel
from ui.session_controller import SessionController
from ui.settings_dialog import SettingsDialog
from ui.theme import set_property, set_variant
from ui.theme.tokens import DARK
from ui.text_utils import strip_emoji_prefix
from ui.widgets.composer import Composer
from ui.worker_guard import request_stop, wait_briefly, worker_is_running
from core.web_research import extract_urls
from workers.chat_worker import ChatWorker
from workers.code_worker import CodeWorker
from workers.models_worker import ModelsWorker
from workers.plan_worker import PlanWorker
from workers.research_worker import ResearchWorker
from workers.web_answer_worker import WebAnswerWorker
from workers.title_worker import TitleWorker

_MAX_LIVE_VIEWS = 6


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


class ChatPage(QWidget):
    """Full interactive chat interface connected to local Ollama."""

    def __init__(self, settings: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._models: list[str] = []
        self._current_chat_id = str(uuid.uuid4())
        self._controllers: dict[str, SessionController] = {}
        self._saved_chat_signatures: dict[str, tuple[Any, ...]] = {}
        self._view_last_used: dict[str, float] = {}
        self._deleted_chat_ids: set[str] = set()
        self._active_title_workers: list[TitleWorker] = []

        self._active_code_worker: CodeWorker | None = None
        self._preload_worker: PreloadWorker | None = None

        # Background build tracking (decoupled from active chat view)
        self._build_running = False
        self._building_chat_id: str | None = None
        self._building_plan: CodingPlan | None = None
        self._building_files: dict[str, str] = {}
        self._building_workspace_id: str | None = None

        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(100)
        self._stream_flush_timer.timeout.connect(self._flush_stream_ui)
        self._pending_stream_dirty = False
        self._user_scrolled_away = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._do_scroll_to_bottom)

        self._build_ui()
        self._draft_save_timer = QTimer(self)
        self._draft_save_timer.setInterval(3000)
        self._draft_save_timer.timeout.connect(self._save_streaming_drafts)
        self._draft_save_timer.start()
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._refresh_models)
        self._auto_timer.start(25000)

    @property
    def _active_controller(self) -> SessionController:
        return self._controllers[self._current_chat_id]

    @property
    def _messages(self) -> list[dict[str, Any]]:
        return self._active_controller.session.messages

    @property
    def _current_assistant_widget(self) -> ChatMessageWidget | None:
        view = self._active_controller.view
        return view.streaming_widget if view is not None else None

    @_current_assistant_widget.setter
    def _current_assistant_widget(self, widget: ChatMessageWidget | None) -> None:
        view = self._active_controller.view
        if view is not None:
            view.streaming_widget = widget

    @property
    def _current_assistant_text(self) -> list[str]:
        return self._active_controller.session.streaming

    @_current_assistant_text.setter
    def _current_assistant_text(self, text: list[str]) -> None:
        self._active_controller.session.streaming = text

    @property
    def _last_user_prompt(self) -> str:
        return self._active_controller.session.last_user_prompt

    @_last_user_prompt.setter
    def _last_user_prompt(self, value: str) -> None:
        self._active_controller.session.last_user_prompt = value

    @property
    def _last_user_attachments(self) -> list[dict[str, Any]]:
        return self._active_controller.session.last_user_attachments

    @_last_user_attachments.setter
    def _last_user_attachments(self, value: list[dict[str, Any]]) -> None:
        self._active_controller.session.last_user_attachments = value

    @property
    def _last_extra_system(self) -> str:
        return self._active_controller.session.last_extra_system

    @_last_extra_system.setter
    def _last_extra_system(self, value: str) -> None:
        self._active_controller.session.last_extra_system = value

    @property
    def _workspace_id(self) -> str | None:
        return self._active_controller.session.workspace_id

    @_workspace_id.setter
    def _workspace_id(self, value: str | None) -> None:
        self._active_controller.session.workspace_id = value

    @property
    def _workspace_ids_history(self) -> list[str]:
        return self._active_controller.session.workspace_ids_history

    @_workspace_ids_history.setter
    def _workspace_ids_history(self, value: list[str]) -> None:
        self._active_controller.session.workspace_ids_history = value

    @property
    def _research_project_id(self) -> str | None:
        return self._active_controller.session.research_project_id

    @_research_project_id.setter
    def _research_project_id(self, value: str | None) -> None:
        self._active_controller.session.research_project_id = value

    @property
    def _pending_project_target(self) -> dict[str, Any] | None:
        return self._active_controller.session.pending_project_target

    @_pending_project_target.setter
    def _pending_project_target(self, value: dict[str, Any] | None) -> None:
        self._active_controller.session.pending_project_target = value

    @property
    def _pending_attachments(self) -> list[dict[str, Any]]:
        return self._active_controller.session.pending_attachments

    @_pending_attachments.setter
    def _pending_attachments(self, value: list[dict[str, Any]]) -> None:
        self._active_controller.session.pending_attachments = value

    @property
    def _build_files(self) -> dict[str, str]:
        return self._active_controller.session.build_files

    @_build_files.setter
    def _build_files(self, value: dict[str, str]) -> None:
        self._active_controller.session.build_files = value

    @property
    def _last_prompt_eval_tokens(self) -> int:
        return self._active_controller.session.last_prompt_eval_tokens

    @_last_prompt_eval_tokens.setter
    def _last_prompt_eval_tokens(self, value: int) -> None:
        self._active_controller.session.last_prompt_eval_tokens = value

    @property
    def _is_hybrid_mode(self) -> bool:
        return self._active_controller.session.is_hybrid_mode

    @_is_hybrid_mode.setter
    def _is_hybrid_mode(self, value: bool) -> None:
        self._active_controller.session.is_hybrid_mode = value

    @property
    def _current_chat_title(self) -> str | None:
        return self._active_controller.session.title

    @_current_chat_title.setter
    def _current_chat_title(self, value: str | None) -> None:
        self._active_controller.session.title = value

    @property
    def _current_chat_has_ai_title(self) -> bool:
        return self._active_controller.session.ai_title

    @_current_chat_has_ai_title.setter
    def _current_chat_has_ai_title(self, value: bool) -> None:
        self._active_controller.session.ai_title = value

    @property
    def _pending_research_topic(self) -> str:
        return self._active_controller.session.pending_research_topic

    @_pending_research_topic.setter
    def _pending_research_topic(self, value: str) -> None:
        self._active_controller.session.pending_research_topic = value

    @property
    def _pending_research_url(self) -> str:
        return self._active_controller.session.pending_research_url

    @_pending_research_url.setter
    def _pending_research_url(self, value: str) -> None:
        self._active_controller.session.pending_research_url = value

    @property
    def _pending_research_model(self) -> str:
        return self._active_controller.session.pending_research_model

    @_pending_research_model.setter
    def _pending_research_model(self, value: str) -> None:
        self._active_controller.session.pending_research_model = value

    @property
    def _pending_research_num_ctx(self) -> int:
        return self._active_controller.session.pending_research_num_ctx

    @_pending_research_num_ctx.setter
    def _pending_research_num_ctx(self, value: int) -> None:
        self._active_controller.session.pending_research_num_ctx = value

    @property
    def _pending_research_plan(self) -> Any:
        return self._active_controller.session.pending_research_plan

    @_pending_research_plan.setter
    def _pending_research_plan(self, value: Any) -> None:
        self._active_controller.session.pending_research_plan = value

    @property
    def _pending_research_project_id(self) -> str | None:
        return self._active_controller.session.pending_research_project_id

    @_pending_research_project_id.setter
    def _pending_research_project_id(self, value: str | None) -> None:
        self._active_controller.session.pending_research_project_id = value

    @property
    def _research_activity_log(self) -> list[str]:
        return self._active_controller.session.research_activity_log

    @_research_activity_log.setter
    def _research_activity_log(self, value: list[str]) -> None:
        self._active_controller.session.research_activity_log = value

    @property
    def _last_research_progress_time(self) -> float:
        return self._active_controller.session.last_research_progress_time

    @_last_research_progress_time.setter
    def _last_research_progress_time(self, value: float) -> None:
        self._active_controller.session.last_research_progress_time = value

    @property
    def _research_start_mono(self) -> float:
        return self._active_controller.session.research_start_mono

    @_research_start_mono.setter
    def _research_start_mono(self, value: float) -> None:
        self._active_controller.session.research_start_mono = value

    @property
    def _active_plan(self) -> CodingPlan | None:
        return self._active_controller.coding_plan

    @_active_plan.setter
    def _active_plan(self, value: CodingPlan | None) -> None:
        self._active_controller.coding_plan = value

    @property
    def _active_chat_worker(self) -> Any:
        return self._active_controller.chat_worker

    @_active_chat_worker.setter
    def _active_chat_worker(self, worker: Any) -> None:
        self._active_controller.chat_worker = worker

    @property
    def _active_plan_worker(self) -> Any:
        return self._active_controller.plan_worker

    @_active_plan_worker.setter
    def _active_plan_worker(self, worker: Any) -> None:
        self._active_controller.plan_worker = worker

    @property
    def _active_research_worker(self) -> Any:
        return self._active_controller.research_worker

    @_active_research_worker.setter
    def _active_research_worker(self, worker: Any) -> None:
        self._active_controller.research_worker = worker

    @property
    def _classify_worker(self) -> Any:
        return self._active_controller.classify_worker

    @_classify_worker.setter
    def _classify_worker(self, worker: Any) -> None:
        self._active_controller.classify_worker = worker

    @property
    def _route_worker(self) -> Any:
        return self._active_controller.route_worker

    @_route_worker.setter
    def _route_worker(self, worker: Any) -> None:
        self._active_controller.route_worker = worker

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.shell_splitter.setHandleWidth(1)
        main_layout.addWidget(self.shell_splitter)

        # Left Sidebar
        self.sidebar = ChatSidebar()
        self.sidebar.new_chat_clicked.connect(self._start_new_chat)
        self.sidebar.chat_selected.connect(self._load_conversation)
        self.sidebar.chat_delete_requested.connect(self._on_chat_delete_requested)
        self.sidebar.chat_deleted.connect(self._on_chat_deleted)
        self.sidebar.settings_clicked.connect(self._open_settings)
        self.sidebar.toggle_requested.connect(self._toggle_sidebar)
        self.sidebar.chat_renamed.connect(self._on_chat_renamed)
        self.shell_splitter.addWidget(self.sidebar)

        # Chat workspace and library occupy separate pages; plans and files live
        # in a collapsible inspector beside the conversation.
        self.page_stack = QStackedWidget()
        self.shell_splitter.addWidget(self.page_stack)
        self.chat_workspace = QWidget()
        right_layout = QVBoxLayout(self.chat_workspace)
        right_layout.setContentsMargins(12, 8, 12, 12)
        right_layout.setSpacing(6)

        # Top Bar
        self.top_bar = ChatTopBar()
        self.top_bar.model_changed.connect(self._on_model_changed)
        self.top_bar.refresh_clicked.connect(self._refresh_models)
        self.top_bar.mode_changed.connect(lambda m: self._settings.update({"coding_mode": m}))
        self.top_bar.keep_warm_requested.connect(self._on_keep_warm_requested)
        self.top_bar.connection_expired.connect(self._on_connection_expired)
        self.top_bar.sidebar_toggle_requested.connect(self._toggle_sidebar)
        self.top_bar.inspector_toggle_requested.connect(self._toggle_inspector)
        self.top_bar.artifacts_requested.connect(self._show_artifact_popover)
        self.top_bar.set_mode(self._settings.get("coding_mode", "auto"))
        self.top_bar.set_low_power_mode(bool(self._settings.get("low_power_mode", False)))
        self.sidebar.set_low_power_mode(bool(self._settings.get("low_power_mode", False)))
        self.top_bar.set_keep_alive_seconds(get_effective_keep_alive_seconds(self._settings))
        self.top_bar.set_internet_access(bool(self._settings.get("internet_access", False)))
        right_layout.addWidget(self.top_bar)

        # Main conversation column
        self.chat_views_stack = QStackedWidget()

        self.plan_panel = PlanPanel()
        self.plan_panel.plan_approved.connect(self._on_plan_approved)

        self.research_plan_panel = ResearchPlanPanel()
        self.research_plan_panel.plan_approved.connect(self._on_research_plan_approved)

        self.plan_stack = QStackedWidget()
        self.plan_stack.addWidget(self.plan_panel)
        self.plan_stack.addWidget(self.research_plan_panel)

        self.artifacts_panel = ArtifactsPanel()

        self.research_panel = ResearchPanel()
        self.research_panel.resume_requested.connect(self._resume_research)
        self.chat_artifacts_panel = ArtifactListPanel("No artifacts in this chat yet.")
        self.chat_artifacts_panel.open_requested.connect(self._open_artifact_viewer)

        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setSpacing(8)
        center_row.addWidget(self.chat_views_stack, 1)

        self.inspector = QWidget()
        self.inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.inspector.setObjectName("Inspector")
        self.inspector.setFixedWidth(DARK.sizes.inspector_width)
        inspector_layout = QVBoxLayout(self.inspector)
        inspector_layout.setContentsMargins(8, 0, 0, 0)
        inspector_layout.setSpacing(8)
        inspector_header = QHBoxLayout()
        inspector_header.setSpacing(4)
        self.btn_inspector_plan = QPushButton("Plan")
        self.btn_inspector_plan.setIcon(icon("list-checks"))
        self.btn_inspector_plan.setCheckable(True)
        self.btn_inspector_files = QPushButton("Artifacts")
        self.btn_inspector_files.setIcon(icon("package"))
        self.btn_inspector_files.setCheckable(True)
        for button in (self.btn_inspector_plan, self.btn_inspector_files):
            button.setProperty("variant", "ghost")
            inspector_header.addWidget(button)
        self.btn_inspector_plan.clicked.connect(self._show_plan)
        self.btn_inspector_files.clicked.connect(self._show_files)
        self.btn_inspector_close = QToolButton()
        self.btn_inspector_close.setIcon(icon("x"))
        self.btn_inspector_close.setToolTip("Close inspector (Ctrl+Shift+B)")
        self.btn_inspector_close.setProperty("variant", "ghost")
        self.btn_inspector_close.setProperty("iconOnly", "true")
        self.btn_inspector_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_inspector_close.clicked.connect(self._toggle_inspector)
        inspector_header.addStretch()
        inspector_header.addWidget(self.btn_inspector_close)
        inspector_layout.addLayout(inspector_header)

        self.inspector_stack = QStackedWidget()
        self.inspector_stack.addWidget(self.plan_stack)
        self.inspector_stack.addWidget(self.chat_artifacts_panel)
        inspector_layout.addWidget(self.inspector_stack, 1)
        self.inspector.hide()
        center_row.addWidget(self.inspector)
        right_layout.addLayout(center_row, 1)

        library_page = QWidget()
        library_layout = QVBoxLayout(library_page)
        library_layout.setContentsMargins(12, 8, 12, 12)
        library_header = QHBoxLayout()
        back_to_chat = QPushButton("Back to chat")
        back_to_chat.setIcon(icon("chevron-right"))
        back_to_chat.clicked.connect(self._show_chat)
        library_header.addWidget(back_to_chat)
        library_header.addStretch()
        library_layout.addLayout(library_header)

        self.library_artifacts_panel = ArtifactListPanel(
            "No projects in the Library yet.",
            filterable=True,
        )
        self.library_artifacts_panel.open_requested.connect(self._open_artifact_viewer)
        self._refresh_library_artifacts()

        self.library_tabs = QTabWidget()
        self.library_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.library_tabs.addTab(self.research_panel, "Research")
        self.library_tabs.addTab(self.library_artifacts_panel, "Artifacts")
        library_layout.addWidget(self.library_tabs, 1)

        self.page_stack.addWidget(self.chat_workspace)
        self.page_stack.addWidget(library_page)
        self.navigation = Navigator(
            self.page_stack,
            self.chat_workspace,
            library_page,
            self.inspector_stack,
            self.plan_stack,
            self.chat_artifacts_panel,
            self.inspector,
        )
        self.plan_panel.plan_rejected.connect(self.navigation.show_chat)
        self.research_plan_panel.plan_cancelled.connect(self.navigation.show_chat)
        self.sidebar.library_clicked.connect(self.navigation.show_library)
        self.shell_splitter.setCollapsible(0, True)
        self.shell_splitter.setSizes([DARK.sizes.sidebar_width, 900])
        self._sidebar_animation = QPropertyAnimation(self.sidebar, b"maximumWidth", self)
        self._sidebar_animation.setDuration(160)
        self._sidebar_collapsed = False
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setMaximumWidth(DARK.sizes.sidebar_width)
        self._create_session_controller(ChatSession(id=self._current_chat_id))

        # Removable Attachment Chips Bar
        self.attachment_bar = QWidget()
        self.attachment_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.attachment_bar_layout = QHBoxLayout(self.attachment_bar)
        self.attachment_bar_layout.setContentsMargins(4, 2, 4, 2)
        self.attachment_bar_layout.setSpacing(6)
        self.attachment_bar.setVisible(False)
        right_layout.addWidget(self.attachment_bar)

        self.composer = Composer(self)
        self.input_edit = self.composer.input_edit
        self.composer.send_requested.connect(self._send_message)
        self.composer.stop_requested.connect(self._stop_generation)

        self.btn_attach = self.composer.btn_attach
        self.attach_menu = QMenu(self)
        self.artifacts_submenu = QMenu("Artifacts", self.attach_menu)
        self.artifacts_submenu.aboutToShow.connect(self._populate_artifacts_submenu)
        self.attach_menu.addMenu(self.artifacts_submenu)
        self.btn_attach.setMenu(self.attach_menu)

        right_layout.addWidget(self.composer)
        self._activate_controller(self._active_controller)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        shortcuts = (
            ("Ctrl+N", self._start_new_chat),
            ("Ctrl+K", self.sidebar.search_input.setFocus),
            ("Ctrl+B", self._toggle_sidebar),
            ("Ctrl+Shift+B", self._toggle_inspector),
            ("Ctrl+,", self._open_settings),
            ("Esc", self._stop_generation),
        )
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)

    def _refresh_library_artifacts(self) -> None:
        busy_id = self._building_workspace_id if self._build_running else None
        self.library_artifacts_panel.set_projects(
            workspace.list_workspaces(kind="code"),
            busy_id,
        )

    def _refresh_chat_artifacts(self, chat_id: str) -> list[dict]:
        projects = workspace.list_workspaces_for_chat(chat_id)
        controller = self._controllers.get(chat_id)
        if controller is not None:
            history = set(controller.session.workspace_ids_history)
            if controller.session.workspace_id:
                history.add(controller.session.workspace_id)
            known = {str(project.get("id") or project.get("slug")) for project in projects}
            if history - known:
                projects = [
                    project
                    for project in workspace.list_workspaces(kind="code")
                    if str(project.get("id") or project.get("slug")) in history
                ] + projects
                projects = list({str(p.get("id") or p.get("slug")): p for p in projects}.values())
        busy_id = self._building_workspace_id if self._building_chat_id == chat_id else None
        if chat_id == self._current_chat_id:
            self.chat_artifacts_panel.set_projects(projects, busy_id)
            self.top_bar.set_artifact_count(len(projects))
        self._refresh_library_artifacts()
        return projects

    def _show_artifact_popover(self) -> None:
        chat_id = self._current_chat_id
        projects = self._refresh_chat_artifacts(chat_id)
        menu = QMenu(self)
        if projects:
            for project in projects:
                action = menu.addAction(icon("folder"), str(project.get("name") or "Project"))
                workspace_id = str(project.get("id") or project.get("slug") or "")
                action.triggered.connect(
                    lambda checked=False, wid=workspace_id: self._open_artifact_viewer(wid)
                )
        else:
            empty = menu.addAction("No artifacts in this chat yet")
            empty.setEnabled(False)
        menu.addSeparator()
        library_action = menu.addAction("Show all in Library")
        library_action.triggered.connect(self._show_artifact_library)
        menu.popup(self.top_bar.btn_artifacts.mapToGlobal(
            self.top_bar.btn_artifacts.rect().bottomLeft()
        ))

    def _show_artifact_library(self) -> None:
        self._refresh_library_artifacts()
        self.library_tabs.setCurrentWidget(self.library_artifacts_panel)
        self.navigation.show_library()

    def _open_artifact_viewer(self, workspace_id: str) -> None:
        host = self.window().centralWidget()
        if host is None:
            return
        viewer = ArtifactViewer(host)
        viewer.open_workspace(workspace_id)

    def _show_chat(self) -> None:
        self.navigation.show_chat()
        self.input_edit.setFocus()

    def _show_plan(self) -> None:
        self.navigation.show_plan()
        self.btn_inspector_plan.setChecked(True)
        self.btn_inspector_files.setChecked(False)

    def _show_files(self) -> None:
        self.inspector.setFixedWidth(DARK.sizes.inspector_width)
        self._refresh_chat_artifacts(self._current_chat_id)
        self.navigation.show_files()
        self.btn_inspector_plan.setChecked(False)
        self.btn_inspector_files.setChecked(True)

    def _restore_navigation_view(self, view: str) -> None:
        self.navigation.restore_view(view)
        self.btn_inspector_plan.setChecked(view == "plan")
        self.btn_inspector_files.setChecked(view == "files")

    def _toggle_inspector(self) -> None:
        if self.inspector.isVisible():
            self.inspector.hide()
        elif self.inspector_stack.currentWidget() is self.chat_artifacts_panel:
            self._show_files()
        else:
            self._show_plan()

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self._sidebar_collapsed = collapsed
        width = 0 if collapsed else DARK.sizes.sidebar_width
        self.sidebar.setMaximumWidth(width)
        if collapsed or self.shell_splitter.sizes()[0] == 0:
            self.shell_splitter.setSizes([width, max(1, self.width() - width)])

    @property
    def sidebar_collapsed(self) -> bool:
        return self._sidebar_collapsed

    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        target_width = 0 if self._sidebar_collapsed else DARK.sizes.sidebar_width
        low_power = bool(self._settings.get("low_power_mode", False))
        if low_power:
            self.sidebar.setMaximumWidth(target_width)
            self.shell_splitter.setSizes([target_width, max(1, self.width() - target_width)])
            return
        self._sidebar_animation.stop()
        if target_width and self.sidebar.maximumWidth() == 0:
            self.shell_splitter.setSizes([target_width, max(1, self.width() - target_width)])
        self._sidebar_animation.setStartValue(self.sidebar.width())
        self._sidebar_animation.setEndValue(target_width)
        self._sidebar_animation.start()

    def _on_chat_renamed(self, chat_id: str, title: str) -> None:
        controller = self._controllers.get(chat_id)
        if controller is not None:
            controller.session.title = title
            controller.session.ai_title = True
            self._on_controller_persist_needed(controller)
            return
        if data := load_chat(chat_id):
            if data.get("title") != title:
                data["title"] = title
                data["ai_title"] = True
                save_chat(data)
        self.sidebar.refresh_list()

    def _create_session_controller(
        self,
        session: ChatSession,
        view: ChatView | None = None,
    ) -> SessionController:
        controller = SessionController(session, view, parent=self)
        controller.persist_needed.connect(self._on_controller_persist_needed)
        controller.chat_complete.connect(self._on_controller_chat_complete)
        controller.worker_failed.connect(self._on_controller_worker_failed)
        controller.plan_complete.connect(self._on_controller_plan_complete)
        controller.classify_done.connect(self._on_controller_classify_done)
        controller.route_done.connect(self._on_controller_route_done)
        controller.compaction_notice.connect(self._on_controller_compaction_notice)
        controller.generating_progress.connect(
            lambda count, cid=session.id: self._on_controller_token_count(cid, count)
        )
        controller.status_text.connect(
            lambda text, cid=session.id: self._show_session_status(cid, text)
        )
        controller.became_busy.connect(
            lambda _controller, cid=session.id: self._update_sidebar_session(cid)
        )
        controller.became_idle.connect(
            self._on_controller_became_idle
        )
        controller.unread_changed.connect(
            lambda cid, unread: self.sidebar.set_chat_state(cid, unread=unread)
        )
        self._controllers[session.id] = controller
        self._saved_chat_signatures[session.id] = self._chat_save_signature(
            controller.persist_dict()
        )
        self._ensure_chat_view(controller)
        return controller

    def _ensure_chat_view(self, controller: SessionController) -> ChatView:
        if controller.view is None:
            view = ChatView(self.chat_views_stack)
            view.rebuild(
                controller.session.messages,
                controller.session.current_stream_text()
                if controller.session.status == "generating"
                else controller.session.draft_assistant or "",
            )
            controller.view = view
            self.chat_views_stack.addWidget(view)
            view.retry_clicked.connect(
                lambda cid=controller.chat_id: self._on_retry_clicked_for_chat(cid)
            )
            view.load_earlier_clicked.connect(
                lambda cid=controller.chat_id: self._load_earlier_for_chat(cid)
            )
        return controller.view

    def _activate_controller(self, controller: SessionController) -> None:
        previous = self._controllers.get(self._current_chat_id)
        if previous is not None and previous is not controller:
            previous.set_visible(False)
        self._current_chat_id = controller.chat_id
        self.sidebar.set_active_chat(controller.chat_id)
        view = self._ensure_chat_view(controller)
        controller.set_visible(True)
        self._view_last_used[controller.chat_id] = time.monotonic()
        self.chat_views_stack.setCurrentWidget(view)
        self.scroll_area = view.scroll_area
        self.history_container = view.history_container
        self.history_layout = view.history_layout
        self._apply_view_limit()
        self._update_sidebar_session(controller.chat_id)
        projects = self._refresh_chat_artifacts(controller.chat_id)
        for project in projects:
            self._upsert_inline_artifact_card(
                controller,
                str(project.get("id") or project.get("slug") or ""),
                self._building_chat_id == controller.chat_id
                and str(project.get("id") or project.get("slug") or "") == self._building_workspace_id,
            )
        self._sync_active_controls()

    def _upsert_inline_artifact_card(
        self,
        controller: SessionController,
        workspace_id: str,
        busy: bool,
    ) -> None:
        project = workspace.get_workspace(workspace_id)
        if project is None:
            return
        view = self._ensure_chat_view(controller)
        for index in range(view.history_layout.count()):
            old = view.history_layout.itemAt(index).widget()
            if (
                isinstance(old, ArtifactCard)
                and old.property("workspaceId") == workspace_id
            ):
                view.history_layout.removeWidget(old)
                old.deleteLater()
                break
        card = ArtifactCard(project, busy=busy, parent=view.history_container)
        card.setObjectName("InlineArtifactCard")
        card.setProperty("workspaceId", workspace_id)
        card.open_requested.connect(self._open_artifact_viewer)
        view.history_layout.insertWidget(view.history_layout.count() - 1, card)

    def _apply_view_limit(self) -> None:
        live = [
            controller
            for controller in self._controllers.values()
            if controller.view is not None
        ]
        while len(live) > _MAX_LIVE_VIEWS:
            candidates = [c for c in live if c.chat_id != self._current_chat_id]
            if not candidates:
                return
            oldest = min(candidates, key=lambda c: self._view_last_used.get(c.chat_id, 0))
            view = oldest.view
            if view is not None:
                self.chat_views_stack.removeWidget(view)
                view.deleteLater()
                oldest.view = None
            live.remove(oldest)

    def _sync_active_controls(self) -> None:
        controller = self._active_controller
        busy = controller.is_busy() or (
            self._build_running and self._building_chat_id == controller.chat_id
        )
        self.composer.set_generating(busy)
        self.composer.set_send_enabled(not busy)
        if busy:
            self.top_bar.set_working()

    def _update_sidebar_session(self, chat_id: str) -> None:
        controller = self._controllers.get(chat_id)
        if controller is not None:
            self.sidebar.set_chat_state(
                chat_id,
                status=controller.session.status,
                unread=controller.session.unread,
            )

    def _on_controller_became_idle(self, controller: SessionController) -> None:
        self._update_sidebar_session(controller.chat_id)
        if controller.chat_id == self._current_chat_id:
            self._sync_active_controls()
            if controller.session.status in ("idle", "error"):
                self.top_bar.set_idle()

    def _show_session_status(self, chat_id: str, text: str) -> None:
        if chat_id == self._current_chat_id:
            self.top_bar.show_status(text)

    def _on_controller_persist_needed(self, controller: SessionController) -> None:
        if (
            controller.chat_id in self._deleted_chat_ids
            or self._controllers.get(controller.chat_id) is not controller
        ):
            return
        session = controller.session
        if not session.messages:
            return
        title = session.title or next(
            (
                str(msg.get("content", ""))[:30]
                + ("…" if len(str(msg.get("content", ""))) > 30 else "")
                for msg in session.messages
                if msg.get("role") == "user"
            ),
            "New Chat",
        )
        session.title = title
        data = controller.persist_dict()
        data["title"] = title
        data["ai_title"] = session.ai_title
        if not session.workspace_id:
            data["artifacts"] = dict(session.build_files)
        if controller.view is not None:
            controller.view.update_message_model(session.messages)
        signature = self._chat_save_signature(data)
        saved_signature = self._saved_chat_signatures.get(controller.chat_id)
        previous_messages = saved_signature[0] if saved_signature is not None else []
        new_message = len(data["messages"]) > len(previous_messages)
        if saved_signature != signature:
            save_chat(data)
            self._saved_chat_signatures[controller.chat_id] = signature
        self.sidebar.upsert_chat(
            controller.chat_id,
            title,
            status=session.status,
            unread=session.unread,
            new_message=new_message,
        )

    @staticmethod
    def _chat_save_signature(data: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            copy.deepcopy(data.get(key))
            for key in ("messages", "title", "plan", "status")
        )

    def _save_streaming_drafts(self) -> None:
        for controller in tuple(self._controllers.values()):
            session = controller.session
            if session.is_busy() and (session.streaming or session.draft_assistant):
                self._on_controller_persist_needed(controller)

    def _on_controller_chat_complete(
        self,
        controller: SessionController,
        full_content: str,
        meta: dict[str, Any],
    ) -> None:
        session = controller.session
        if session.is_hybrid_mode and controller.coding_plan:
            plan = controller.coding_plan
            full_content += (
                f"\n\n---\n### Project Plan Ready: {plan.goal} ({len(plan.steps)} steps)\n\n"
                + "\n".join(
                    f"- **Step {step.step}**: {step.description} → `{step.file}`"
                    for step in plan.steps
                )
                + "\n\nA complete project plan has been prepared in the **Plan** tab! "
                "Switch to the Plan tab and click **Approve & Start Coding** whenever you're ready."
            )
            session.is_hybrid_mode = False
            if session.messages and session.messages[-1].get("role") == "assistant":
                session.messages[-1]["content"] = full_content
                if controller.view is not None:
                    controller.view.rebuild(session.messages)
        if controller is self._active_controller:
            self.top_bar.set_idle()
            self.top_bar.hide_status()
            self._last_prompt_eval_tokens = int(
                meta.get("prompt_eval_count", 0) or 0
            ) + int(meta.get("eval_count", 0) or 0)
            self._update_token_meter()
            if controller.view is not None:
                controller.view.add_retry_button()
        self._on_controller_persist_needed(controller)
        if not session.ai_title and session.last_user_prompt:
            self._request_ai_chat_title(
                controller.chat_id,
                session.last_user_prompt,
                session.model or self.top_bar.current_model(),
                self._settings.get("ollama_url", "http://localhost:11434"),
                session.num_ctx,
            )
        if session.sequential_plan_args:
            args = session.sequential_plan_args
            session.sequential_plan_args = None
            self._run_for_controller(
                controller,
                self._start_sequential_plan,
                *args,
                full_content,
                meta,
            )

    def _on_controller_worker_failed(self, controller: SessionController, err_msg: str) -> None:
        if controller is self._active_controller:
            self.top_bar.set_idle()
            self.top_bar.hide_status()
            if controller.view is not None:
                controller.view.add_retry_button()
        self._on_controller_persist_needed(controller)

    def _on_controller_plan_complete(
        self,
        controller: SessionController,
        plan: CodingPlan,
    ) -> None:
        callback = self._on_hybrid_plan_ready if controller.plan_hybrid else self._on_plan_ready
        self._run_for_controller(controller, callback, plan)

    def _on_controller_classify_done(
        self,
        controller: SessionController,
        task_type: str,
    ) -> None:
        args = controller.classify_args or ()
        self._run_for_controller(controller, self._on_classify_done, task_type, *args)

    def _on_controller_route_done(
        self,
        controller: SessionController,
        decision: str,
    ) -> None:
        args = controller.route_args or ()
        self._run_for_controller(controller, self._on_route_resolved, decision, *args)

    def _connect_for_controller(
        self,
        signal: Any,
        controller: SessionController,
        callback: Any,
    ) -> None:
        signal.connect(
            lambda *args, c=controller, fn=callback: self._run_for_controller(c, fn, *args)
        )

    def _on_controller_compaction_notice(
        self,
        controller: SessionController,
        old_tokens: int,
        new_tokens: int,
    ) -> None:
        if controller.view is not None:
            controller.view.add_notice(
                f"Compacted conversation (~{old_tokens:,} → ~{new_tokens:,} tokens)"
            )
        if controller is self._active_controller:
            self._update_token_meter()

    def _on_controller_token_count(self, chat_id: str, count: int) -> None:
        if chat_id == self._current_chat_id:
            self.top_bar.notify_activity()
            self.top_bar.show_status(f"Generating ({count} tokens)…")

    def _on_retry_clicked_for_chat(self, chat_id: str) -> None:
        if chat_id != self._current_chat_id:
            return
        self._on_retry_clicked()

    def _load_earlier_for_chat(self, chat_id: str) -> None:
        controller = self._controllers.get(chat_id)
        if controller and controller.view:
            controller.view.load_earlier()

    def _set_controller_context(self, controller: SessionController) -> None:
        self._current_chat_id = controller.chat_id
        view = self._ensure_chat_view(controller)
        self.scroll_area = view.scroll_area
        self.history_container = view.history_container
        self.history_layout = view.history_layout

    def _run_for_controller(
        self,
        controller: SessionController,
        callback: Any,
        *args: Any,
    ) -> None:
        """Run a legacy page callback against its owning session, never the visible chat."""
        if (
            controller.chat_id in self._deleted_chat_ids
            or self._controllers.get(controller.chat_id) is not controller
        ):
            return
        if controller.chat_id == self._current_chat_id:
            callback(*args)
            return
        visible_id = self._current_chat_id
        visible_view = self.navigation.current_view
        status_text = self.top_bar.lbl_status.text()
        status_visible = self.top_bar.status_container.isVisible()
        progress_value = self.top_bar.progress_bar.value()
        status_icon_pixmap = self.top_bar._status_icon.pixmap()
        status_icon_hidden = self.top_bar._status_icon.isHidden()
        status_spinner_hidden = self.top_bar._status_spinner.isHidden()
        connection = self.top_bar.lbl_connection
        connection_state = (
            connection.text(),
            connection.toolTip(),
            connection.property("state"),
            self.top_bar._is_connected,
            self.top_bar._is_working,
            self.top_bar._seconds_remaining,
            self.top_bar._keep_alive_timer.isActive(),
        )
        try:
            self._set_controller_context(controller)
            callback(*args)
        finally:
            self._set_controller_context(self._controllers[visible_id])
            self._restore_navigation_view(visible_view)
            self.top_bar.lbl_status.setText(status_text)
            if status_icon_pixmap is None:
                self.top_bar._status_icon.clear()
            else:
                self.top_bar._status_icon.setPixmap(status_icon_pixmap)
            self.top_bar._status_icon.setVisible(not status_icon_hidden)
            self.top_bar._status_spinner.setVisible(not status_spinner_hidden)
            self.top_bar.status_container.setVisible(status_visible)
            self.top_bar.progress_bar.setValue(progress_value)
            (
                badge_text,
                badge_tooltip,
                badge_state,
                self.top_bar._is_connected,
                self.top_bar._is_working,
                self.top_bar._seconds_remaining,
                timer_active,
            ) = connection_state
            connection.setText(badge_text)
            connection.setToolTip(badge_tooltip)
            set_property(connection, "state", badge_state)
            if timer_active and not self.top_bar._keep_alive_timer.isActive():
                self.top_bar._keep_alive_timer.start()
            elif not timer_active:
                self.top_bar._keep_alive_timer.stop()

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
        if self._build_running or worker_is_running(self._active_chat_worker):
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
                act = self.artifacts_submenu.addAction(icon("folder"), name)
                wid = ws.get("id")
                act.triggered.connect(lambda checked=False, w_id=wid, w_name=name: self._add_attachment(w_id, w_name, "code"))

        if research_projects:
            for rp in research_projects:
                t = (rp.get("title") or rp.get("topic") or "Research")[:30]
                dt = rp.get("date", "")[:10]
                act = self.artifacts_submenu.addAction(icon("flask"), f"{t} [Research] ({dt})")
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
            chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            chip.setProperty("role", "card")
            chip.setObjectName("AttachmentChip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(6, 2, 6, 2)
            chip_layout.setSpacing(4)
            icon_name = "flask" if att.get("kind") == "research" else "file"
            icon_label = QLabel()
            icon_label.setPixmap(icon(icon_name, DARK.colors.text_secondary, 16).pixmap(16, 16))
            chip_layout.addWidget(icon_label)
            lbl = QLabel(att["name"])
            lbl.setProperty("role", "muted")
            chip_layout.addWidget(lbl)

            btn_del = QPushButton()
            btn_del.setIcon(icon("x"))
            btn_del.setFixedSize(32, 32)
            btn_del.setToolTip("Remove attachment")
            set_variant(btn_del, "danger")
            set_property(btn_del, "iconOnly", "true")
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            att_id = att["id"]
            btn_del.clicked.connect(lambda checked=False, a_id=att_id: self._remove_attachment(a_id))
            chip_layout.addWidget(btn_del)

            self.attachment_bar_layout.addWidget(chip)

        self.attachment_bar_layout.addStretch()
        self.attachment_bar.setVisible(True)

    # ── Chat & Tab Navigation ──────────────────────────────────────────

    def _start_new_chat(self) -> None:
        self._save_current_conversation()
        controller = self._create_session_controller(ChatSession())
        self._activate_controller(controller)
        self._render_attachment_chips()
        self.artifacts_panel.clear()
        self.sidebar.clear_selection()
        self._update_token_meter()

        self._active_plan = None
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        self.navigation.show_chat()

    def _on_chat_delete_requested(self, chat_id: str) -> None:
        self._deleted_chat_ids.add(chat_id)
        controller = self._controllers.get(chat_id)
        if controller and controller.is_busy():
            controller.stop_chat_workers()
        if chat_id == self._building_chat_id and worker_is_running(self._active_code_worker):
            request_stop(self._active_code_worker)
            wait_briefly(self._active_code_worker, 2000)
            self._build_running = worker_is_running(self._active_code_worker)
            if not self._build_running:
                self._building_chat_id = None
                self._building_plan = None
                self._building_files = {}
                self._building_workspace_id = None
                self._active_code_worker = None

    def _on_chat_deleted(self, chat_id: str) -> None:
        """Handle a chat being deleted from the sidebar."""
        controller = self._controllers.pop(chat_id, None)
        self._saved_chat_signatures.pop(chat_id, None)
        was_current = chat_id == self._current_chat_id
        if controller:
            if controller.is_busy():
                controller.stop_chat_workers()
            if controller.view is not None:
                self.chat_views_stack.removeWidget(controller.view)
                controller.view.deleteLater()
            self._view_last_used.pop(chat_id, None)
        if was_current:
            self._current_chat_id = str(uuid.uuid4())
            new_controller = self._create_session_controller(
                ChatSession(id=self._current_chat_id)
            )
            self._activate_controller(new_controller)
            self.artifacts_panel.clear()
            self.sidebar.clear_selection()
            self._update_token_meter()
            self.plan_panel.btn_approve.setText("Approve & Start Coding")
            self.plan_panel.btn_approve.setEnabled(True)
            self.navigation.show_chat()

    def _load_conversation(self, chat_id: str) -> None:
        if chat_id == self._current_chat_id:
            if self._build_running or self._build_files:
                self._show_files()
            return

        self._save_current_conversation()
        controller = self._controllers.get(chat_id)
        if controller is None:
            data = load_chat(chat_id)
            if not data:
                return
            session = ChatSession.from_dict(data)
            controller = self._create_session_controller(session)
            if saved_plan := data.get("plan"):
                controller.coding_plan = reconstruct_plan(saved_plan)
                self._saved_chat_signatures[chat_id] = self._chat_save_signature(
                    controller.persist_dict()
                )

        self._activate_controller(controller)
        self.navigation.show_chat()
        data = controller.persist_dict()
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
            self._show_files()
        else:
            self.artifacts_panel.clear()
            self._build_files = dict(controller.session.build_files)
            if self._build_files:
                for fname, code in self._build_files.items():
                    self.artifacts_panel.add_file(fname, code)
            else:
                self.artifacts_panel.show_chat_summary([])

            if saved_plan := data.get("plan"):
                self._active_plan = controller.coding_plan or reconstruct_plan(saved_plan)
                self.plan_panel.set_plan(self._active_plan)
                for s in self._active_plan.steps:
                    if s.completed:
                        self.plan_panel.mark_step_completed(s.step)
                all_done = all(s.completed for s in self._active_plan.steps)
                if all_done:
                    self.plan_panel.btn_approve.setText("Build completed")
                    self.plan_panel.btn_approve.setEnabled(False)
                else:
                    self.plan_panel.btn_approve.setText("▶ Resume Building")
                    self.plan_panel.btn_approve.setEnabled(True)
            else:
                self._active_plan = None
                self.plan_panel.btn_approve.setText("Approve & Start Coding")
                self.plan_panel.btn_approve.setEnabled(True)

            if work_session := load_session(chat_id):
                self._restore_session(work_session)
                self._show_files() if self._build_files else self.navigation.show_chat()
            else:
                self._show_files() if self._build_files else self.navigation.show_chat()

        self._update_token_meter()
        self._scroll_to_bottom()

    def _restore_session(self, session: dict) -> None:
        """Restore a saved work session: show artifacts scoped to this chat, mark plan steps, offer resume."""
        plan = reconstruct_plan(session)
        self._active_plan = plan
        self._build_files = dict(self._active_controller.session.build_files)
        if not self._build_files:
            self._build_files = dict(session.get("completed_files", {}))
        self.artifacts_panel.clear()
        if self._build_files:
            for fname, code in self._build_files.items():
                self.artifacts_panel.add_file(fname, code)
        else:
            self.artifacts_panel.show_chat_summary([])
        self.plan_panel.set_plan(plan)
        for s in plan.steps:
            if s.completed:
                self.plan_panel.mark_step_completed(s.step)
        all_done = all(s.completed for s in plan.steps)
        if all_done:
            self.plan_panel.btn_approve.setText("Build completed")
            self.plan_panel.btn_approve.setEnabled(False)
        else:
            self.plan_panel.btn_approve.setText("▶ Resume Building")
            self.plan_panel.btn_approve.setEnabled(True)

    # ── Retry Button ───────────────────────────────────────────────────

    def _add_retry_button(self) -> None:
        """Add a retry button below the last assistant message."""
        view = self._active_controller.view
        if view is None or not any(m.get("role") == "user" for m in self._messages):
            return
        view.add_retry_button()

    def _remove_retry_button(self) -> None:
        """Remove existing retry button row if present."""
        view = self._active_controller.view
        if view is not None:
            view.remove_retry_button()

    def _on_retry_clicked(self) -> None:
        """Re-run the last user prompt by removing the old response and resending."""
        prompt, attachments = self._active_controller.retry_last_turn()
        if not prompt:
            return
        self._last_user_prompt = prompt
        self.composer.set_text(prompt)
        if attachments:
            self._pending_attachments = list(attachments)
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
        text = self.composer.text()
        model = self.top_bar.current_model()
        if not text or not model:
            return
        if self._active_controller.is_busy() or (
            self._build_running and self._building_chat_id == self._current_chat_id
        ):
            self.top_bar.show_status("Waiting for the current reply")
            return

        self.composer.clear()
        self._last_user_prompt = text
        self._active_controller.session.model = model

        # Capture attachments for this message (and restore on retry)
        attachments = list(self._pending_attachments)
        self._last_user_attachments = attachments
        self._pending_attachments = []
        self._render_attachment_chips()

        # Build attachment context (capped at 40% of num_ctx) for injection into the chat
        url = self._settings.get("ollama_url", "http://localhost:11434")
        num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        self._active_controller.session.num_ctx = num_ctx
        if attachments:
            budget = max(200, int(num_ctx * 0.40))
            ids = [a["id"] for a in attachments]
            names = " ".join(a.get("name", "") for a in attachments)
            self._last_extra_system = build_attachment_context(ids, user_prompt=names, budget_tokens=budget)
        else:
            self._last_extra_system = ""

        # Remove any existing retry button before adding new messages
        self._remove_retry_button()

        self._active_controller.session.add_user(text, attachments)
        att_for_widget = attachments or None
        self._active_controller.view.add_user(text, attachments=att_for_widget)
        self._save_current_conversation()

        self.top_bar.set_working()
        self.composer.set_generating(True)
        self.composer.set_send_enabled(False)

        self._current_assistant_text = []

        # AI title is requested after the main reply finishes (see _on_chat_finished / _on_plan_ready)
        # to avoid model reload races when num_ctx differs.

        est_tokens = estimate_messages_tokens(self._active_controller.session.model_messages())
        if self._last_extra_system:
            est_tokens += estimate_messages_tokens([{"role": "system", "content": self._last_extra_system}])
        if is_compaction_needed(est_tokens, num_ctx, threshold_ratio=0.75):
            self.top_bar.show_status(f"Checking tokens ({est_tokens:,}/{num_ctx:,}) — compacting history…", 30)

        mode = self.top_bar.current_mode()
        extracted_urls = extract_urls(text)
        if mode in ("auto", "chat") and extracted_urls:
            if not self._settings.get("internet_access", False):
                err = (
                    "**Web access is disabled in Settings.**\n\n"
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
                controller = self._active_controller
                waw.status_message.connect(
                    lambda status, cid=controller.chat_id: self._show_session_status(cid, status)
                )
                controller.bind_chat_worker(waw)
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
                    self.top_bar.show_status("Analyzing task type…")
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                    cw = self._classify_worker = ClassifyWorker(url, model, text, num_ctx, parent=self)
                    self._active_controller.bind_classify_worker(
                        cw, (text, url, model, num_ctx)
                    )
                    cw.finished.connect(cw.deleteLater)
                    cw.start()

        self._scroll_to_bottom(force=True)

    def _show_internet_blocked_error(self) -> None:
        """Show an inline error when Research is selected but internet is disabled."""
        err = (
            "**Web Research is disabled.**\n\n"
            "Enable it in **Settings > Internet Access** to use Research mode.\n\n"
            "Ollambo is offline by default for privacy — web access is opt-in."
        )
        self._active_controller.session.add_error(err)
        if self._active_controller.view is not None:
            self._active_controller.view.add_assistant(err)
        self._on_controller_persist_needed(self._active_controller)
        self._sync_active_controls()

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
                        self._add_notice_row(f"Continuing research on **{proj.get('name', main_q)}**")
                    else:
                        # new or ambiguous → start a fresh research project
                        self._research_project_id = None
                        self._add_notice_row("Starting a new research project")
            except Exception:
                pass

        self.top_bar.show_status(f"Deep Research ({depth.capitalize()}): drafting research plan…")

        self._research_activity_log = [
            "Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            f"Preset: {depth.capitalize()} Depth",
            "Drafting research plan and skeptic checklist with local LLM…",
        ]

        init_md = (
            f"### Deep Research: Initializing\n\n"
            f"- **Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **Preset:** {depth.capitalize()}\n"
            f"- **Status:** Drafting research plan, sub-questions, and skeptic checklist with local LLM…\n\n"
            f"*(Outlining research questions before querying DuckDuckGo)*"
        )
        self._active_controller.session.draft_assistant = init_md

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
        controller = self._active_controller
        controller.bind_research_worker(rw)
        self._connect_for_controller(rw.plan_ready, controller, self._on_research_plan_ready)
        self._connect_for_controller(rw.progress, controller, self._on_research_progress_detailed)
        self._connect_for_controller(rw.status_message, controller, self._on_research_progress)
        self._connect_for_controller(rw.research_done, controller, self._on_research_finished)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._on_controller_persist_needed(controller)
        self._start_research_heartbeat()
        self._scroll_to_bottom(force=True)

    @Slot(object, str)
    def _on_research_plan_ready(self, plan: Any, project_id: str) -> None:
        self._pending_research_plan = plan
        self._pending_research_project_id = project_id
        self._active_controller.session.status = "idle"
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
            self.top_bar.show_status("Research plan approved — starting web search & execution…")
            msg = (
                f"### Research Plan Drafted & Auto-Approved\n\n"
                f"**Core Question:** {plan.main_question}\n\n"
                f"**Sub-Questions Investigated ({len(plan.sub_questions)}):**\n{sq_bullets}\n\n"
                f"- **Adversarial Skeptic Checklist:** {myths_str}\n\n"
                f"*Web search and evidence collection in progress…*"
            )
            if self._current_assistant_widget:
                self._current_assistant_widget.set_content(msg, is_final=True)
            self._active_controller.session.draft_assistant = msg
            self._scroll_to_bottom()
        else:
            self.plan_stack.setCurrentWidget(self.research_plan_panel)
            self.top_bar.show_status("Research plan ready — please review and approve.")

            msg = (
                f"### Proposed Research Plan\n\n"
                f"**Core Question:** {plan.main_question}\n\n"
                f"**Sub-Questions to Investigate ({len(plan.sub_questions)}):**\n{sq_bullets}\n\n"
                f"- **Adversarial Skeptic Checklist:** {myths_str}\n\n"
                f"Click **Approve & Start Deep Research Now** below to begin, or open the **Plan** tab to customize sub-questions."
            )
            if self._current_assistant_widget:
                self._current_assistant_widget.set_content(msg, is_final=True)
            self._active_controller.session.draft_assistant = None
            self._active_controller.view.streaming_widget = None

            btn_box = QWidget()
            btn_layout = QHBoxLayout(btn_box)
            btn_layout.setContentsMargins(0, 4, 0, 4)
            btn_layout.setSpacing(8)

            btn_approve = QPushButton("Approve & Start Deep Research Now")
            btn_approve.setIcon(icon("arrow-up"))
            set_variant(btn_approve, "primary")
            btn_edit = QPushButton("Edit in Plan tab")
            btn_edit.setIcon(icon("pencil"))

            def _start_approved():
                btn_approve.setEnabled(False)
                btn_edit.setEnabled(False)
                btn_approve.setText("Deep Research running…")
                self._on_research_plan_approved(plan)

            btn_approve.clicked.connect(_start_approved)
            btn_edit.clicked.connect(self._show_plan)

            btn_layout.addWidget(btn_approve)
            btn_layout.addWidget(btn_edit)
            btn_layout.addStretch()

            self.history_layout.insertWidget(self.history_layout.count() - 1, btn_box)
            self._messages.append({"role": "assistant", "content": msg})
            self._save_current_conversation()
            self._scroll_to_bottom(force=True)

    def _on_research_plan_approved(self, approved_plan: Any) -> None:
        self.navigation.show_chat()
        depth = self.top_bar.research_depth()
        url = getattr(self, "_pending_research_url", "http://localhost:11434")
        model = getattr(self, "_pending_research_model", self.top_bar.current_model())
        num_ctx = getattr(self, "_pending_research_num_ctx", 8192)
        topic = getattr(self, "_pending_research_topic", "")
        pid = getattr(self, "_pending_research_project_id", "")

        self.top_bar.show_status("Starting deep research execution…")
        self._research_activity_log = [
            "Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            f"Depth: {depth.capitalize()}",
            "Research plan approved — launching investigative engine…",
        ]

        start_md = (
            f"### Deep Research: Starting execution\n\n"
            f"- **Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **Depth:** {depth.capitalize()}\n"
            f"- **Status:** Launching investigative engine and DuckDuckGo queries…\n\n"
            f"#### Live Activity Feed:\n"
            f"- Initializing web search queries across {len(approved_plan.sub_questions)} sub-questions\n"
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
        controller = self._active_controller
        controller.bind_research_worker(rw)
        self._connect_for_controller(rw.progress, controller, self._on_research_progress_detailed)
        self._connect_for_controller(rw.status_message, controller, self._on_research_progress)
        self._connect_for_controller(rw.research_done, controller, self._on_research_finished)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._on_controller_persist_needed(controller)
        self._start_research_heartbeat()
        self._scroll_to_bottom(force=True)

    @Slot(str, int, int, str, str)
    def _on_research_progress_detailed(self, phase: str, step: int, total: int, msg: str, eta: str) -> None:
        self.top_bar.notify_activity()
        self._last_research_progress_time = time.monotonic()
        clean_msg, _ = strip_emoji_prefix(msg)
        self.research_panel.update_active_step(phase, step, total, clean_msg)

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
        self.top_bar.show_status(f"{stage_prefix} [{step}/{total}]: {clean_msg}{eta_suffix}", pct)

        if not hasattr(self, "_research_activity_log") or self._research_activity_log is None:
            self._research_activity_log = []

        if clean_msg:
            is_dup = any(clean_msg in entry for entry in self._research_activity_log[-30:])
            if not is_dup:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%H:%M:%S")
                self._research_activity_log.append(f"[{ts}] {clean_msg}")

        depth = self.top_bar.research_depth().capitalize()
        recent_logs = self._research_activity_log[-8:]
        log_lines = "\n".join(f"- {line}" for line in recent_logs)

        card_md = (
            f"### Deep Research: {stage_prefix} [{step}/{total}]\n\n"
            f"- **Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
            f"- **Depth:** {depth}\n"
            f"- **Progress:** Step {step} of {total}{eta_suffix}\n"
            f"- **Current Action:** {clean_msg}\n\n"
            f"#### Live Activity Feed:\n"
            f"{log_lines}"
        )
        self._active_controller.session.draft_assistant = card_md
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(card_md, is_final=True)
            self._scroll_to_bottom()

    @Slot(str)
    def _on_research_progress(self, msg: str) -> None:
        self.top_bar.notify_activity()
        # Don't overwrite progress bar with indeterminate status if detailed progress arrived recently
        now = time.monotonic()
        last = getattr(self, "_last_research_progress_time", 0)
        clean_msg, _ = strip_emoji_prefix(msg)
        if now - last < 2.0:
            pass  # skip show_status to preserve percentage in top bar
        else:
            self.top_bar.show_status(clean_msg)
        if hasattr(self, "_research_activity_log") and self._research_activity_log is not None:
            if clean_msg:
                is_dup = any(clean_msg in entry for entry in self._research_activity_log[-30:])
                if not is_dup:
                    import datetime as _dt
                    ts = _dt.datetime.now().strftime("%H:%M:%S")
                    self._research_activity_log.append(f"[{ts}] {clean_msg}")

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
        still_running = any(
            worker_is_running(controller.research_worker)
            for controller in self._controllers.values()
        )
        if not still_running and hasattr(self, "_research_heartbeat_timer"):
            self._research_heartbeat_timer.stop()

    def _on_heartbeat_tick(self) -> None:
        """Update elapsed time in status bar; show 'still working' if stale."""
        controller = self._controllers.get(self._current_chat_id)
        if controller is None or not worker_is_running(controller.research_worker):
            self._stop_research_heartbeat()
            return
        elapsed = time.monotonic() - controller.session.research_start_mono
        mm = int(elapsed) // 60
        ss = int(elapsed) % 60
        elapsed_str = f"{mm:02d}:{ss:02d}"

        last_prog = controller.session.last_research_progress_time
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
        self._active_controller.session.status = "idle"
        self._active_controller.session.draft_assistant = None

        # Track this as the chat's active research project for continue-or-new routing
        self._research_project_id = project_id
        self._save_current_conversation()

        from core.research_store import get_project
        proj = get_project(project_id) or {}
        title = proj.get("title", "Research Report")
        sources_cnt = proj.get("source_count", 0)

        finish_msg = (
            f"### Deep Research complete: {title}\n\n"
            f"- **Web Access:** Completed\n"
            f"- **Sources evaluated & verified:** {sources_cnt}\n"
            f"- **Report sections:** 11 sections including Executive Summary, Key Findings, "
            f"Disputed Claims, Mermaid Diagram, Glossary, and Learning Aids.\n"
            f"- **Saved to:** Research Library tab\n"
        )
        if self._current_assistant_widget:
            if self._active_controller.view is not None:
                self._active_controller.view.finish_stream(finish_msg)
            else:
                self._current_assistant_widget.set_content(finish_msg, is_final=True)
                self._current_assistant_widget = None

        # Action button to view report
        btn_open = QPushButton("Open report in Research Library")
        btn_open.setIcon(icon("file"))
        set_variant(btn_open, "primary")
        btn_open.clicked.connect(
            lambda: (self.navigation.show_library(), self.research_panel.open_report(project_id))
        )
        self.history_layout.insertWidget(self.history_layout.count() - 1, btn_open)

        self._messages.append({"role": "assistant", "content": finish_msg})
        self._update_token_meter()
        self.top_bar.hide_status()
        self._save_current_conversation()
        self._cleanup_workers_state()
        self._add_retry_button()

    def _resume_research(self, project_id: str) -> None:
        """Resume an interrupted research project from the Research Library."""
        url = self._settings.get("ollama_url", "http://localhost:11434")
        model = self.top_bar.current_model()
        num_ctx = getattr(self, "_active_num_ctx", int(self._settings.get("num_ctx", 8192)))
        self.navigation.show_chat()
        self.top_bar.show_status("Resuming research from checkpoint…")

        self._research_activity_log = [
            "Web Access: ACTIVE (DuckDuckGo Live Search & Content Extraction)",
            "Resuming research project from saved checkpoint…",
        ]

        resume_md = (
            f"### Deep Research: Resuming\n\n"
            f"- **Web Access:** `ACTIVE` (DuckDuckGo Live Search & Content Extraction)\n"
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
        controller = self._active_controller
        controller.bind_research_worker(rw)
        self._connect_for_controller(rw.progress, controller, self._on_research_progress_detailed)
        self._connect_for_controller(rw.status_message, controller, self._on_research_progress)
        self._connect_for_controller(rw.research_done, controller, self._on_research_finished)
        rw.finished.connect(rw.deleteLater)
        rw.start()
        self._last_research_progress_time = time.monotonic()
        self._on_controller_persist_needed(controller)
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
            self._active_controller.bind_plan_worker(w)
            w.finished.connect(w.deleteLater)
            w.start()
        else:  # both
            pw = self._active_plan_worker = PlanWorker(url, model, text, num_ctx, existing_context=existing_context, parent=self)
            self._active_controller.bind_plan_worker(pw, hybrid=True)
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
            self.top_bar.show_status("Deciding whether to continue or start a new project…")
            rw = self._route_worker = RouteWorker(url, model, text, ws_summary, num_ctx, parent=self)
            controller = self._active_controller
            controller.bind_route_worker(
                rw, (task_type, text, url, model, num_ctx, ws_summary)
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
            self.top_bar.show_status("Architect model is analyzing task…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "Analyzing request & planning project…")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            else:
                self._current_assistant_widget.set_content("Analyzing request & planning project…")
            self._route_and_plan("code", text, url, model, num_ctx)
        elif task_type == "both":
            is_low_power = bool(self._settings.get("low_power_mode", False))
            extra_sys = getattr(self, "_last_extra_system", "")
            if is_low_power:
                self.top_bar.show_status("Replying in chat (low-power: planning will run next)…")
                if not self._current_assistant_widget:
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                cw = self._active_chat_worker = ChatWorker(
                    url, model, self._active_controller.session.model_messages(), {"num_ctx": num_ctx, "low_power_mode": True},
                    self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
                )
                controller = self._active_controller
                controller.session.sequential_plan_args = (url, model, text, num_ctx)
                controller.bind_chat_worker(cw)
                cw.finished.connect(cw.deleteLater)
                cw.start()
            else:
                self.top_bar.show_status("Explaining in chat and planning project…")
                if not self._current_assistant_widget:
                    self._current_assistant_widget = ChatMessageWidget("assistant", "")
                    self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
                cw = self._active_chat_worker = ChatWorker(
                    url, model, self._active_controller.session.model_messages(), {"num_ctx": num_ctx},
                    self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
                )
                self._active_controller.bind_chat_worker(cw)
                cw.finished.connect(cw.deleteLater)
                cw.start()

                self._route_and_plan("both", text, url, model, num_ctx)
        else:
            extra_sys = getattr(self, "_last_extra_system", "")
            if not is_compaction_needed(estimate_messages_tokens(self._messages), num_ctx, threshold_ratio=0.75):
                self.top_bar.show_status("Connecting to local model…")
            if not self._current_assistant_widget:
                self._current_assistant_widget = ChatMessageWidget("assistant", "")
                self.history_layout.insertWidget(self.history_layout.count() - 1, self._current_assistant_widget)
            cw = self._active_chat_worker = ChatWorker(
                url, model, self._active_controller.session.model_messages(), {"num_ctx": num_ctx},
                self._settings.get("summarizer_model", ""), extra_system=extra_sys, parent=self,
            )
            self._active_controller.bind_chat_worker(cw)
            cw.finished.connect(cw.deleteLater)
            cw.start()

    def _start_sequential_plan(self, url: str, model: str, text: str, num_ctx: int, full_chat: str, meta: dict) -> None:
        """Run planner sequentially after chat reply finishes in low power mode."""
        self.top_bar.show_status("Low-power mode: planning project…")
        self._route_and_plan("both", text, url, model, num_ctx)

    @Slot(object)
    def _on_hybrid_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self._show_plan()
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        if getattr(self, "_is_hybrid_mode", False) and self._current_assistant_widget:
            footer = (
                f"\n\n---\n### Project Plan Ready: {plan.goal} ({len(plan.steps)} steps)\n\n"
                + "\n".join(f"- **Step {s.step}**: {s.description} → `{s.file}`" for s in plan.steps)
                + "\n\nA complete project plan has been prepared in the **Plan** tab! Switch to the Plan tab and click **Approve & Start Coding** whenever you're ready to build."
            )
            curr = self._current_assistant_widget.get_content()
            if "Project Plan Ready" not in curr:
                updated = curr + footer
                self._current_assistant_widget.set_content(updated, is_final=True)
                if self._messages and self._messages[-1].get("role") == "assistant":
                    self._messages[-1]["content"] = updated
            self._is_hybrid_mode = False
        self.top_bar.show_status(f"Answer ready; project plan prepared ({len(plan.steps)} steps) in Plan tab", 100)
        self._save_current_conversation()

    @Slot(object)
    def _on_plan_ready(self, plan: CodingPlan) -> None:
        self.top_bar.notify_activity()
        self._active_plan = plan
        self.plan_panel.set_plan(plan)
        self.plan_panel.btn_approve.setText("Approve & Start Coding")
        self.plan_panel.btn_approve.setEnabled(True)
        self.plan_stack.setCurrentWidget(self.plan_panel)
        self._show_plan()
        self.top_bar.show_status(f"Plan ready ({len(plan.steps)} steps) — review in Plan tab", 100)
        plan_msg = (
            f"**Plan Ready: {plan.goal}** ({len(plan.steps)} steps)\n\n"
            + "\n".join(f"  {s.step}. {s.description} → `{s.file}`" for s in plan.steps)
            + "\n\nReview the plan in the **Plan** tab and click **Approve & Start Coding** to begin building."
        )
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content(plan_msg)
        if self._active_controller.view is not None:
            self._active_controller.view.streaming_widget = None
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
                self._add_notice_row(f"Previous build saved as **{old_name}**")

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
            self._add_notice_row(f"Started a new project **{name}**")
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
                self._add_notice_row(f"Continuing in project **{ws_name}**")

        self._building_files = dict(self._build_files)
        self._building_workspace_id = self._workspace_id
        if self._building_workspace_id:
            workspace.link_chat(self._building_workspace_id, self._building_chat_id)
            self._refresh_chat_artifacts(self._building_chat_id)
            self._upsert_inline_artifact_card(
                self._active_controller,
                self._building_workspace_id,
                busy=True,
            )
        self._active_controller.session.status = "building"
        self._update_sidebar_session(self._current_chat_id)

        self.plan_panel.btn_approve.setText("Building in Progress…")
        self.plan_panel.btn_approve.setEnabled(False)
        self._show_files()
        self.composer.set_generating(True)

        # Resume step: only when the SAME plan already has completed steps
        same_plan_has_completed = bool(self._active_plan and self._active_plan is plan and any(s.completed for s in plan.steps))
        start = get_resume_step(plan) if same_plan_has_completed else 1
        existing = dict(self._building_files)
        total = len(plan.steps)
        label = plan.steps[start - 1].file if start <= total else "?"
        action = "Resuming" if start > 1 else "Starting"
        self.top_bar.show_status(f"{action} [{start}/{total}] {label}…", 0)

        self.sidebar.show_wip(self._building_chat_id)
        save_session(
            self._building_chat_id,
            plan,
            self._building_files,
            start,
            workspace_id=self._building_workspace_id,
        )
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
        w.finished.connect(partial(self._on_code_worker_finished, w))
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_code_worker_finished(self, worker: CodeWorker) -> None:
        if self._active_code_worker is worker:
            self._active_code_worker = None

    def _on_step_started(self, step_num: int, filename: str, total: int) -> None:
        if self._current_chat_id == self._building_chat_id:
            self.top_bar.notify_activity()
            pct = int(((step_num - 1) / total) * 100) if total else 0
            self.top_bar.show_status(f"[{step_num}/{total}] Writing {filename}…", pct)

    def _on_part_started(self, filename: str, part: int, max_parts: int) -> None:
        if self._current_chat_id == self._building_chat_id:
            self.top_bar.notify_activity()
            self.artifacts_panel.show_continuation_progress(filename, part, max_parts)
            self.top_bar.show_status(f"Continuing {filename} ({part}/{max_parts})…")

    def _on_test_progress(self, filename: str, msg: str) -> None:
        if self._current_chat_id == self._building_chat_id:
            self.top_bar.notify_activity()
            self.top_bar.show_status(msg)
            self.artifacts_panel.show_test_status(filename, msg)

    @Slot(str, str, int)
    def _on_file_checkpoint(self, filename: str, code: str, step_num: int) -> None:
        target_id = self._building_chat_id or self._current_chat_id
        if target_id in self._deleted_chat_ids:
            return
        if self._current_chat_id == target_id:
            self.top_bar.notify_activity()
        self._building_files[filename] = code
        target_controller = self._controllers.get(target_id)
        if target_controller:
            target_controller.session.build_files[filename] = code

        # Persist session (plan state + next step + workspace_id) - NO full chat reload per file
        if self._building_plan:
            for s in self._building_plan.steps:
                if s.step == step_num or s.file == filename:
                    s.completed = True
            save_session(
                target_id, self._building_plan, self._building_files, step_num + 1,
                workspace_id=self._building_workspace_id,
            )

        # Update UI if user is actively viewing the building chat
        if self._current_chat_id == target_id:
            self._build_files[filename] = code
            self.artifacts_panel.add_file(filename, code)
            self.plan_panel.mark_step_completed(step_num)
            total = len(self._building_plan.steps) if self._building_plan else 1
            pct = int((step_num / total) * 100) if total else 0
            self.top_bar.show_status(f"{filename} ({step_num}/{total})", pct)
        self._refresh_chat_artifacts(target_id)
        workspace_id = self._building_workspace_id or (
            target_controller.session.workspace_id if target_controller is not None else None
        )
        if target_controller is not None and target_controller.view is not None and workspace_id:
            self._upsert_inline_artifact_card(
                target_controller,
                workspace_id,
                busy=True,
            )

    @Slot(dict)
    def _on_all_files_completed(self, files: dict[str, str]) -> None:
        target_id = self._building_chat_id or self._current_chat_id
        if target_id in self._deleted_chat_ids:
            self._build_running = False
            if self._current_chat_id == target_id:
                self.top_bar.set_idle()
            self._building_chat_id = None
            self._building_plan = None
            self._building_files = {}
            self._building_workspace_id = None
            self._deleted_chat_ids.discard(target_id)
            return
        self._build_running = False

        complete_session(target_id)
        self.sidebar.hide_wip()

        # Prefer files from the workspace on disk (source of truth)
        ws_id = self._building_workspace_id
        ws_files = workspace.read_all(ws_id) if ws_id else files
        summary = generate_project_walkthrough(self._building_plan or self._active_plan, ws_files)

        # Include project folder path in the summary
        if ws_id:
            wp = workspace.workspace_path(ws_id)
            if wp:
                summary = f"{summary}\n\n**Project folder:** `{wp}`"

        target_controller = self._controllers.get(target_id)
        if target_controller:
            target_controller.session.messages.append({"role": "assistant", "content": summary})
            target_controller.session.workspace_id = ws_id
            target_controller.session.build_files = dict(ws_files)
            target_controller.session.status = "idle"
            if target_controller.view is not None and target_id != self._current_chat_id:
                target_controller.view.add_assistant(summary)
            target_controller.mark_unread_if_hidden()
            self._on_controller_persist_needed(target_controller)
        elif c_data := load_chat(target_id):
            c_data.setdefault("messages", []).append({"role": "assistant", "content": summary})
            c_data["artifacts"] = {}
            c_data["workspace_id"] = ws_id
            save_chat(c_data)

        if self._current_chat_id == target_id:
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("assistant", summary))
            self.top_bar.set_idle()
            self.top_bar.show_status(f"All {len(ws_files)} files completed. Walkthrough generated.", 100)
            QTimer.singleShot(4000, self.top_bar.hide_status)
            self.plan_panel.btn_approve.setText("Build completed")
            self.plan_panel.btn_approve.setEnabled(False)
            self._add_retry_button()

        self._building_chat_id = None
        self._building_plan = None
        self._building_files = {}
        self._building_workspace_id = None
        self._deleted_chat_ids.discard(target_id)
        self._refresh_chat_artifacts(target_id)
        if target_controller is not None and ws_id:
            self._upsert_inline_artifact_card(target_controller, ws_id, busy=False)
        self._sync_active_controls()

    @Slot(str)
    def _on_build_error(self, err_msg: str) -> None:
        target_id = self._building_chat_id or self._current_chat_id
        if target_id in self._deleted_chat_ids:
            self._build_running = False
            if self._current_chat_id == target_id:
                self.top_bar.set_idle()
            self._building_chat_id = None
            self._building_plan = None
            self._building_files = {}
            self._building_workspace_id = None
            self._deleted_chat_ids.discard(target_id)
            return
        self._build_running = False
        failed_workspace_id = self._building_workspace_id

        err = f"Build error: {err_msg}\nProgress saved — click **Resume Building** in Plan tab."
        target_controller = self._controllers.get(target_id)
        if target_controller:
            target_controller.session.messages.append({"role": "assistant", "content": err, "error": True})
            target_controller.session.status = "idle"
            if target_controller.view is not None and target_id != self._current_chat_id:
                target_controller.view.add_assistant(err)
            target_controller.mark_unread_if_hidden()
            self._on_controller_persist_needed(target_controller)
        elif c_data := load_chat(target_id):
            c_data.setdefault("messages", []).append({"role": "assistant", "content": err})
            save_chat(c_data)

        if self._building_plan:
            save_session(
                target_id,
                self._building_plan,
                self._building_files,
                get_resume_step(self._building_plan),
                workspace_id=self._building_workspace_id,
            )
            self.sidebar.show_wip(target_id)

        if self._current_chat_id == target_id:
            self.history_layout.insertWidget(self.history_layout.count() - 1, ChatMessageWidget("assistant", err))
            self.top_bar.set_idle()
            self.top_bar.hide_status()
            if self._active_plan:
                self.plan_panel.btn_approve.setText("▶ Resume Building")
                self.plan_panel.btn_approve.setEnabled(True)
            self._add_retry_button()

        self._building_chat_id = None
        self._building_plan = None
        self._building_files = {}
        self._building_workspace_id = None
        self._refresh_chat_artifacts(target_id)
        if target_controller is not None and failed_workspace_id:
            self._upsert_inline_artifact_card(
                target_controller,
                failed_workspace_id,
                busy=False,
            )
        self._sync_active_controls()

    @Slot(str)
    def _on_token_received(self, token: str) -> None:
        self._active_controller.on_token(token)

    def _flush_stream_ui(self) -> None:
        """Render batched tokens smoothly at ~40fps without UI stutter."""
        if not self._pending_stream_dirty:
            return
        self._pending_stream_dirty = False
        if self._current_assistant_widget:
            self._current_assistant_widget.set_content("".join(self._current_assistant_text), is_final=False)
        self.top_bar.show_status(f"Generating ({len(self._current_assistant_text)} tokens)…")
        # Use debounced scroll instead of direct call to avoid layout thrashing
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    @Slot(int, int)
    def _on_compaction_started(self, current_tok: int, threshold_tok: int) -> None:
        """Show compaction in the status bar and progress bar."""
        self.top_bar.show_status(
            f"Compacting conversation ({current_tok:,} tokens exceeded 75% limit)…",
            40,
        )

    def _add_notice_row(self, markdown_text: str) -> None:
        """Insert a small notice row into the chat history (not saved to messages)."""
        clean_text, category = strip_emoji_prefix(markdown_text)
        category_icons = {
            "search": "search",
            "fetch": "external-link",
            "read": "file",
            "check": "check",
            "warn": "alert-triangle",
        }
        notice_icon = category_icons.get(category) if category else None
        if notice_icon is None:
            notice_icon = "flask" if "research" in clean_text.lower() else "message"

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        icon_label = QLabel()
        icon_label.setPixmap(icon(notice_icon, DARK.colors.text_secondary, 16).pixmap(16, 16))
        row_layout.addWidget(icon_label)
        try:
            from ui.message_widget import ChatMessageWidget

            notice = ChatMessageWidget("assistant", clean_text)
            row_layout.addWidget(notice, 1)
        except Exception:
            notice = QLabel(clean_text)
            notice.setProperty("role", "muted")
            row_layout.addWidget(notice, 1)
        self.history_layout.insertWidget(self.history_layout.count() - 1, row)

    @Slot(list, int, int)
    def _on_compaction_performed(self, new_messages: list, old_tok: int, new_tok: int) -> None:
        self._active_controller.session.apply_compaction(new_messages)
        self._on_controller_compaction_notice(
            self._active_controller, old_tok, new_tok
        )
        self._on_controller_persist_needed(self._active_controller)

    @Slot(str, dict)
    def _on_chat_finished(self, full_content: str, meta: dict) -> None:
        self._active_controller.on_chat_finished(full_content, meta)

    @Slot(str)
    def _on_worker_error(self, err_msg: str) -> None:
        self._active_controller.on_chat_error(err_msg)

    def _stop_chat_generation(self) -> None:
        """Stop chat and planning workers without interrupting background code builds."""
        self._active_controller.stop_chat_workers()
        if self._active_research_worker is None:
            self._stop_research_heartbeat()

    def _stop_generation(self) -> None:
        """Handle explicit Stop button click."""
        self._stop_chat_generation()

        if worker_is_running(self._active_code_worker) and (
            self._building_chat_id == self._current_chat_id
        ):
            request_stop(self._active_code_worker)
            wait_briefly(self._active_code_worker)

        if self._build_running:
            target_id = self._building_chat_id or self._current_chat_id
            if self._building_plan:
                save_session(
                    target_id,
                    self._building_plan,
                    self._building_files,
                    get_resume_step(self._building_plan),
                    workspace_id=self._building_workspace_id,
                )
                self.sidebar.show_wip(target_id)
            if self._current_chat_id == target_id:
                if worker_is_running(self._active_code_worker):
                    self.plan_panel.btn_approve.setText("Stopping build…")
                    self.plan_panel.btn_approve.setEnabled(False)
                else:
                    self.plan_panel.btn_approve.setText("▶ Resume Building")
                    self.plan_panel.btn_approve.setEnabled(True)

        self.top_bar.hide_status()
        self._cleanup_workers_state()
        self._add_retry_button()
        self._on_controller_persist_needed(self._active_controller)

    def _cleanup_workers_state(self) -> None:
        self._sync_active_controls()

    def _save_current_conversation(self) -> None:
        self._on_controller_persist_needed(self._active_controller)

    def _add_load_earlier_button(self) -> None:
        """Add a 'Load earlier messages' button at the top of the chat history."""
        self._remove_load_earlier_button()
        btn = QPushButton("⬆ Load earlier messages")
        btn.setObjectName("load_earlier_btn")
        btn.setProperty("variant", "ghost")
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
        controller = self._controllers.get(chat_id)
        if controller and controller.session.ai_title:
            return
        existing_data = None
        if controller is None:
            existing_data = load_chat(chat_id)
            if existing_data and existing_data.get("ai_title"):
                return
        # Update memory if this conversation is currently loaded.
        if self._current_chat_id == chat_id:
            self._current_chat_title = title
            self._current_chat_has_ai_title = True
        if controller:
            controller.session.title = title
            controller.session.ai_title = True

        if controller:
            self._on_controller_persist_needed(controller)
        elif c_data := existing_data:
            if c_data.get("title") != title:
                c_data["title"] = title
                c_data["ai_title"] = True
                save_chat(c_data)
        self.sidebar.upsert_chat(chat_id, title)

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
            low_power = bool(self._settings.get("low_power_mode", False))
            self.top_bar.set_low_power_mode(low_power)
            self.sidebar.set_low_power_mode(low_power)
            self.top_bar.set_keep_alive_seconds(
                get_effective_keep_alive_seconds(self._settings)
            )
            self.top_bar.set_internet_access(bool(self._settings.get("internet_access", False)))
            self.top_bar.set_models(
                self._models,
                self._settings.get("default_model", ""),
            )
            self._on_model_changed(self.top_bar.current_model())

    def stop_workers(self) -> None:
        self._auto_timer.stop()
        self._draft_save_timer.stop()
        for controller in tuple(self._controllers.values()):
            if controller.is_busy():
                controller.stop_chat_workers()
            for worker in (
                controller.chat_worker,
                controller.plan_worker,
                controller.research_worker,
                controller.classify_worker,
                controller.route_worker,
            ):
                if worker_is_running(worker):
                    request_stop(worker)
                    wait_briefly(worker)
            self._on_controller_persist_needed(controller)

        if worker_is_running(self._active_code_worker):
            request_stop(self._active_code_worker)
            wait_briefly(self._active_code_worker)
        if self._build_running and self._building_chat_id and self._building_plan:
            save_session(
                self._building_chat_id,
                self._building_plan,
                self._building_files,
                get_resume_step(self._building_plan),
                workspace_id=self._building_workspace_id,
            )

        for worker in tuple(self._active_title_workers):
            if worker_is_running(worker):
                request_stop(worker)
                wait_briefly(worker)
        if worker_is_running(self._preload_worker):
            request_stop(self._preload_worker)
            wait_briefly(self._preload_worker)
        self._stop_research_heartbeat()
