"""
Top bar widget for ChatPage: model picker with auto-refresh, coding mode toggle,
connection warm status indicator (3-5 min inactivity window), token meter,
and live operation progress bar.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.icons import Spinner, StatusIcon, icon
from ui.theme import set_property
from ui.theme.tokens import DARK
from ui.text_utils import strip_emoji_prefix


class AutoUpdateComboBox(QComboBox):
    """QComboBox that signals when the dropdown is clicked/opened to auto-refresh models."""

    popup_opened = Signal()

    def showPopup(self) -> None:
        self.popup_opened.emit()
        super().showPopup()


class ChatTopBar(QWidget):
    """Header controls for model selection, coding mode, keep-alive window, status, and tokens."""

    model_changed = Signal(str)
    refresh_clicked = Signal()
    coding_mode_toggled = Signal(bool)
    artifacts_requested = Signal()
    mode_changed = Signal(str)
    keep_warm_requested = Signal(str)
    connection_expired = Signal(str)
    sidebar_toggle_requested = Signal()
    inspector_toggle_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._seconds_remaining = 180  # 3 minutes keep-alive idle window
        self._is_connected = False
        self._is_working = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # ── Controls row ──────────────────────────────────────────────
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(0, 4, 0, 0)
        ctrl_row.setSpacing(8)
        self.setMinimumHeight(44)

        self.btn_sidebar_toggle = QToolButton()
        self.btn_sidebar_toggle.setIcon(icon("sidebar-left", DARK.colors.text_secondary, 16))
        self.btn_sidebar_toggle.setToolTip("Toggle sidebar (Ctrl+B)")
        self.btn_sidebar_toggle.setProperty("variant", "ghost")
        self.btn_sidebar_toggle.setProperty("iconOnly", "true")
        self.btn_sidebar_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sidebar_toggle.clicked.connect(self.sidebar_toggle_requested.emit)
        ctrl_row.addWidget(self.btn_sidebar_toggle)

        # Auto-updating combo box (triggers refresh on open)
        self.model_combo = AutoUpdateComboBox()
        self.model_combo.setProperty("role", "chip")
        self.model_combo.setMinimumWidth(200)
        self.model_combo.currentTextChanged.connect(self._on_model_text_changed)
        self.model_combo.popup_opened.connect(self.refresh_clicked.emit)
        ctrl_row.addWidget(self.model_combo)

        self.btn_artifacts = QPushButton("Artifacts")
        self.btn_artifacts.setIcon(icon("folder"))
        self.btn_artifacts.setToolTip("Open this chat's projects")
        self.btn_artifacts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_artifacts.clicked.connect(self.artifacts_requested.emit)
        ctrl_row.addWidget(self.btn_artifacts)
        self.artifact_count_badge = QLabel("0")
        self.artifact_count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artifact_count_badge.setMinimumWidth(22)
        self.artifact_count_badge.setProperty("role", "caption")
        self.artifact_count_badge.setStyleSheet(
            f"QLabel {{ background-color: {DARK.colors.raised}; "
            f"border: 1px solid {DARK.colors.border_subtle}; border-radius: 10px; "
            "padding: 1px 5px; }"
        )
        ctrl_row.addWidget(self.artifact_count_badge)

        self.btn_refresh = QToolButton()
        self.btn_refresh.setIcon(icon("refresh", DARK.colors.text_secondary, 16))
        self.btn_refresh.setToolTip("Refresh models")
        self.btn_refresh.setProperty("variant", "ghost")
        self.btn_refresh.setProperty("iconOnly", "true")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        ctrl_row.addWidget(self.btn_refresh)

        self.mode_combo = QComboBox()
        self.mode_combo.setProperty("role", "chip")
        self.mode_combo.addItem(icon("bolt"), "Auto detect", "auto")
        self.mode_combo.addItem(icon("file-code"), "Code mode", "code")
        self.mode_combo.addItem(icon("message"), "Chat only", "chat")
        self.mode_combo.addItem(icon("globe"), "Research", "research")
        self.mode_combo.setToolTip("Mode: Auto lets Ollambo autonomously detect coding vs chat tasks")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl_row.addWidget(self.mode_combo)

        # Research depth selector (visible when Research mode is active)
        self.depth_combo = QComboBox()
        self.depth_combo.addItem(icon("bolt"), "Quick", "quick")
        self.depth_combo.addItem(icon("list-checks"), "Standard", "standard")
        self.depth_combo.addItem(icon("search"), "Thorough", "thorough")
        self.depth_combo.setCurrentIndex(1)  # Default Standard
        self.depth_combo.setToolTip("Research Depth: Quick (3 SQs), Standard (5 SQs + 1 follow-up), Thorough (7 SQs + 2 follow-ups)")
        self.depth_combo.setVisible(False)
        ctrl_row.addWidget(self.depth_combo)

        # Legacy Deep research checkbox (for backwards compatibility)
        self.chk_deep = QCheckBox("Deep")
        self.chk_deep.setVisible(False)

        self._web_badge_container = QWidget()
        web_badge_layout = QHBoxLayout(self._web_badge_container)
        web_badge_layout.setContentsMargins(0, 0, 0, 0)
        web_badge_layout.setSpacing(4)
        web_icon = QLabel()
        web_icon.setPixmap(icon("globe", DARK.colors.accent, 16).pixmap(16, 16))
        web_badge_layout.addWidget(web_icon)
        self.lbl_web_badge = QLabel("Web")
        self.lbl_web_badge.setProperty("role", "info")
        self.lbl_web_badge.setToolTip(
            "Internet access is enabled. Web access works for Research mode and for prompts containing a URL.\n"
            "Disable in Settings > Internet Access to go fully offline."
        )
        self.lbl_web_badge.setVisible(False)
        web_badge_layout.addWidget(self.lbl_web_badge)
        self._web_badge_container.setVisible(False)
        ctrl_row.addWidget(self._web_badge_container)

        # ── Keep-Alive Connection Badge ────────────────────────────────
        self.lbl_connection_icon = StatusIcon(16, DARK.colors.text_tertiary, parent=self)
        ctrl_row.addWidget(self.lbl_connection_icon)
        self.lbl_connection = QLabel("Standby")
        self.lbl_connection.setObjectName("ConnectionBadge")
        self.lbl_connection.setProperty("state", "standby")
        connection_metrics = QFontMetrics(self.lbl_connection.font())
        self.lbl_connection.setFixedWidth(
            max(
                connection_metrics.horizontalAdvance(text)
                for text in (
                    "Standby",
                    "Ready 00:00",
                    "Active (working…)",
                    "Preloading into memory…",
                )
            )
            + 4
        )
        self.lbl_connection.setToolTip(
            "Model stays loaded in RAM/VRAM during a 3-minute inactivity window for instant answers.\n"
            "Automatically unloads after 3m of inactivity to free memory. Window resets on any prompt."
        )
        ctrl_row.addWidget(self.lbl_connection)

        self.btn_warm = QPushButton("Preload")
        self.btn_warm.setIcon(icon("bolt"))
        self.btn_warm.setToolTip("Preload and keep model open in memory for instant responses (3m window)")
        self.btn_warm.clicked.connect(self._on_warm_clicked)
        ctrl_row.addWidget(self.btn_warm)

        ctrl_row.addStretch()

        self.btn_inspector_toggle = QToolButton()
        self.btn_inspector_toggle.setIcon(icon("panel-right", DARK.colors.text_secondary, 16))
        self.btn_inspector_toggle.setToolTip("Toggle inspector (Ctrl+Shift+B)")
        self.btn_inspector_toggle.setProperty("variant", "ghost")
        self.btn_inspector_toggle.setProperty("iconOnly", "true")
        self.btn_inspector_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_inspector_toggle.clicked.connect(self.inspector_toggle_requested.emit)
        ctrl_row.addWidget(self.btn_inspector_toggle)

        self.lbl_token_meter = QLabel("Tokens: 0 / 8192")
        self.lbl_token_meter.setObjectName("TokenMeter")
        self.lbl_token_meter.setProperty("role", "muted")
        self.lbl_token_meter.setFixedWidth(
            QFontMetrics(self.lbl_token_meter.font()).horizontalAdvance(
                "Tokens: ~999,999 / 999,999"
            )
            + 4
        )
        ctrl_row.addWidget(self.lbl_token_meter)
        main_layout.addLayout(ctrl_row)

        # ── Live Status / Thinking Indicator Row ──────────────────────
        self.status_container = QWidget()
        status_layout = QHBoxLayout(self.status_container)
        status_layout.setContentsMargins(0, 2, 0, 2)
        status_layout.setSpacing(8)
        self.status_container.setFixedHeight(20)

        self.lbl_status = QLabel("")
        self.lbl_status.setProperty("role", "muted")
        self._status_icon = QLabel()
        self._status_icon.hide()
        self._status_spinner = StatusIcon(16, DARK.colors.text_secondary, parent=self.status_container)
        self._status_spinner.hide()
        status_layout.addWidget(self._status_spinner)
        status_layout.addWidget(self._status_icon)
        status_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ChatStatusProgressBar")
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        status_layout.addWidget(self.progress_bar)

        self.lbl_status.hide()
        main_layout.addWidget(self.status_container)

        # 1-second countdown timer for inactivity window (default 3m = 180s, configurable)
        self._keep_alive_seconds = 180
        self._keep_alive_timer = QTimer(self)
        self._keep_alive_timer.setInterval(1000)
        self._keep_alive_timer.timeout.connect(self._on_timer_tick)
        self._refresh_keep_alive_copy()
        self._set_connection_icon("message", DARK.colors.text_tertiary)

    def set_keep_alive_seconds(self, seconds: int) -> None:
        """Set idle keep_alive countdown duration in seconds."""
        self._keep_alive_seconds = max(10, int(seconds))
        self._refresh_keep_alive_copy()
        if self._is_connected and not self._is_working:
            self.set_idle()

    def _keep_alive_short(self) -> str:
        secs = getattr(self, "_keep_alive_seconds", 180)
        if secs % 60 == 0:
            return f"{secs // 60}m"
        return f"{secs}s"

    def _refresh_keep_alive_copy(self) -> None:
        label = self._keep_alive_short()
        self.btn_warm.setToolTip(
            f"Preload and keep model open in memory for instant responses ({label} window)"
        )
        if not self._is_connected:
            self.lbl_connection.setToolTip(
                f"Model will load into memory when a prompt is sent, and remain open for a {label} idle window."
            )

    def _on_model_text_changed(self, model: str) -> None:
        self.model_changed.emit(model)

    def _on_warm_clicked(self) -> None:
        model = self.current_model()
        if model:
            self.lbl_connection.setText("Preloading into memory…")
            self._set_connection_icon("bolt", DARK.colors.accent)
            set_property(self.lbl_connection, "state", "warning")
            self.keep_warm_requested.emit(model)

    def set_working(self) -> None:
        """Called when a prompt, plan, or build begins. Pauses idle countdown."""
        self._is_connected = True
        self._is_working = True
        self._keep_alive_timer.stop()
        self._seconds_remaining = self._keep_alive_seconds
        self.lbl_connection.setText("Active (working…)")
        self._set_connection_icon("loader", DARK.colors.accent)
        self.lbl_connection.setToolTip("Model is actively executing a task. The idle countdown is paused.")
        set_property(self.lbl_connection, "state", "active")

    def set_idle(self) -> None:
        """Called when a task finishes. Starts idle countdown."""
        self._is_connected = True
        self._is_working = False
        self._seconds_remaining = self._keep_alive_seconds
        self._update_connection_badge()
        if not self._keep_alive_timer.isActive():
            self._keep_alive_timer.start()

    def notify_activity(self) -> None:
        """Notifies activity and keeps connection state warm."""
        if not getattr(self, "_is_working", False):
            self.set_working()

    def set_connected(self) -> None:
        """Mark model as actively loaded in memory and start idle window."""
        self.set_idle()

    def set_disconnected(self) -> None:
        """Mark model as standby / unloaded."""
        self._is_connected = False
        self._is_working = False
        self._keep_alive_timer.stop()
        self.lbl_connection.setText("Standby")
        self._set_connection_icon("message", DARK.colors.text_tertiary)
        self.lbl_connection.setToolTip(
            f"Model will load into memory when a prompt is sent, and remain open for a {self._keep_alive_short()} idle window."
        )
        set_property(self.lbl_connection, "state", "standby")

    def _on_timer_tick(self) -> None:
        if not self._is_connected or getattr(self, "_is_working", False):
            return
        self._seconds_remaining -= 1
        if self._seconds_remaining <= 0:
            self._keep_alive_timer.stop()
            self._is_connected = False
            self.lbl_connection.setText("Standby")
            self._set_connection_icon("message", DARK.colors.text_tertiary)
            set_property(self.lbl_connection, "state", "standby")
            self.connection_expired.emit(self.current_model())
        else:
            self._update_connection_badge()

    def _update_connection_badge(self) -> None:
        m = self._seconds_remaining // 60
        s = self._seconds_remaining % 60
        time_str = f"{m:02d}:{s:02d}"
        label = self._keep_alive_short()
        self.lbl_connection.setText(f"Ready {time_str}")
        self._set_connection_icon("check", DARK.colors.accent)
        self.lbl_connection.setToolTip(
            f"Model is warm in memory. {label} idle window counting down. Automatically pauses when working."
        )
        set_property(self.lbl_connection, "state", "connected")

    def set_models(self, models: list[str], default_model: str = "") -> None:
        """Update model list without losing current selection."""
        cur = self.current_model()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for m in models:
            self.model_combo.addItem(m)

        # Preserve previous selection or default
        target = cur or default_model
        if target:
            idx = self.model_combo.findText(target)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)

        # If model changed or was empty, emit current selection
        if self.current_model() != cur:
            self.model_changed.emit(self.current_model())

    def current_model(self) -> str:
        return self.model_combo.currentText().strip()

    def set_artifact_count(self, count: int) -> None:
        self.artifact_count_badge.setText(str(count))
        self.artifact_count_badge.setToolTip(
            f"{count} {'artifact' if count == 1 else 'artifacts'} in this chat"
        )

    def _on_mode_changed(self) -> None:
        m = self.current_mode()
        # Show Depth selector only in Research mode
        self.depth_combo.setVisible(m == "research")
        self.chk_deep.setVisible(False)
        self.mode_changed.emit(m)
        self.coding_mode_toggled.emit(m not in ("chat", "research"))

    def current_mode(self) -> str:
        return self.mode_combo.currentData() or "auto"

    def research_depth(self) -> str:
        """Return selected depth: quick, standard, or thorough."""
        return self.depth_combo.currentData() or "standard"

    def is_deep_research(self) -> bool:
        """Return True if depth is not quick (for backward compatibility)."""
        return self.research_depth() != "quick"

    def set_low_power_mode(self, enabled: bool) -> None:
        """Force Quick depth when low power mode is active."""
        self._low_power_mode = enabled
        self._status_spinner.set_low_power_mode(enabled)
        self.lbl_connection_icon.set_low_power_mode(enabled)
        if enabled:
            idx = self.depth_combo.findData("quick")
            if idx >= 0:
                self.depth_combo.setCurrentIndex(idx)
            self.depth_combo.setEnabled(False)
            self.depth_combo.setToolTip("Quick depth is enforced because Low-Power Mode is active.")
        else:
            self.depth_combo.setEnabled(True)
            self.depth_combo.setToolTip("Research Depth: Quick (3 SQs), Standard (5 SQs + 1 follow-up), Thorough (7 SQs + 2 follow-ups)")

    def set_internet_access(self, allowed: bool) -> None:
        """Show/hide web badge and enable/disable Research mode option."""
        self.lbl_web_badge.setVisible(allowed)
        self._web_badge_container.setVisible(allowed)
        idx = self.mode_combo.findData("research")
        if idx >= 0:
            # QComboBox has no built-in item disable; use flags via model
            from PySide6.QtCore import Qt  # local import to avoid top-level cycle
            model = self.mode_combo.model()
            item = model.item(idx)
            if item:
                if allowed:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
                else:
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                    )
                    # If currently in research mode and access was revoked, switch to auto
                    if self.current_mode() == "research":
                        self.set_mode("auto")

    def set_mode(self, mode: str | bool) -> None:
        if isinstance(mode, bool):
            val = "code" if mode else "auto"
        else:
            val = mode if mode in ("auto", "code", "chat", "research") else "auto"
        idx = self.mode_combo.findData(val)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

    def set_coding_mode(self, enabled: bool) -> None:
        self.set_mode(enabled)

    def is_coding_mode(self) -> bool:
        return self.current_mode() in ("auto", "code")

    def update_token_meter(self, est: int, max_ctx: int) -> None:
        pct = (est / max_ctx) if max_ctx > 0 else 0
        self.lbl_token_meter.setText(f"Tokens: ~{est:,} / {max_ctx:,}")
        set_property(
            self.lbl_token_meter,
            "state",
            "warning" if pct >= 0.70 else "normal",
        )

    def show_status(self, text: str, progress: int | None = None) -> None:
        """Display live progress and status message."""
        clean_text, category = strip_emoji_prefix(text)
        self.lbl_status.setText(clean_text)
        self.lbl_status.show()
        category_icons = {
            "search": "search",
            "fetch": "external-link",
            "read": "file",
            "check": "check",
            "warn": "alert-triangle",
        }
        if category is not None:
            self._status_spinner.hide()
            self._status_icon.setPixmap(
                icon(category_icons[category], DARK.colors.text_secondary, 16).pixmap(16, 16)
            )
            self._status_icon.show()
        elif getattr(self, "_low_power_mode", False):
            self._status_spinner.hide()
            self._status_icon.setPixmap(
                icon("message", DARK.colors.text_secondary, 16).pixmap(16, 16)
            )
            self._status_icon.show()
        else:
            self._status_icon.hide()
            self._status_spinner.set_state("loading")
            self._status_spinner.show()
        if progress is None:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.hide()
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, progress)))
            self.progress_bar.show()

    def hide_status(self) -> None:
        self.lbl_status.setText("")
        self.lbl_status.hide()
        self._status_spinner.hide()
        self._status_icon.hide()
        self.progress_bar.hide()

    def _set_connection_icon(self, name: str, color: str) -> None:
        if name == "loader":
            self.lbl_connection_icon.set_color(color)
            self.lbl_connection_icon.set_state("loading")
            return
        self.lbl_connection_icon.set_color(color)
        self.lbl_connection_icon.setPixmap(icon(name, color, 16).pixmap(16, 16))
