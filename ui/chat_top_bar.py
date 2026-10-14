"""
Top bar widget for ChatPage: model picker with auto-refresh, coding mode toggle,
connection warm status indicator (3-5 min inactivity window), token meter,
and live operation progress bar.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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
    mode_changed = Signal(str)
    keep_warm_requested = Signal(str)
    connection_expired = Signal(str)

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
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setSpacing(8)

        # Auto-updating combo box (triggers refresh on open)
        self.model_combo = AutoUpdateComboBox()
        self.model_combo.setMinimumWidth(200)
        self.model_combo.currentTextChanged.connect(self._on_model_text_changed)
        self.model_combo.popup_opened.connect(self.refresh_clicked.emit)
        ctrl_row.addWidget(self.model_combo)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setToolTip("Manually refresh installed Ollama models")
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        ctrl_row.addWidget(self.btn_refresh)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("🤖 Auto Detect", "auto")
        self.mode_combo.addItem("💻 Code Mode", "code")
        self.mode_combo.addItem("💬 Chat Only", "chat")
        self.mode_combo.addItem("🔎 Research", "research")
        self.mode_combo.setToolTip("Mode: Auto lets Ollambo autonomously detect coding vs chat tasks")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl_row.addWidget(self.mode_combo)

        # Research depth selector (visible when Research mode is active)
        self.depth_combo = QComboBox()
        self.depth_combo.addItem("⚡ Quick", "quick")
        self.depth_combo.addItem("🔍 Standard", "standard")
        self.depth_combo.addItem("🔬 Thorough", "thorough")
        self.depth_combo.setCurrentIndex(1)  # Default Standard
        self.depth_combo.setToolTip("Research Depth: Quick (3 SQs), Standard (5 SQs + 1 follow-up), Thorough (7 SQs + 2 follow-ups)")
        self.depth_combo.setVisible(False)
        ctrl_row.addWidget(self.depth_combo)

        # Legacy Deep research checkbox (for backwards compatibility)
        self.chk_deep = QCheckBox("Deep")
        self.chk_deep.setVisible(False)

        # 🌐 Web badge — shown when internet access is enabled
        self.lbl_web_badge = QLabel("🌐 Web")
        self.lbl_web_badge.setToolTip(
            "Internet access is enabled. Web access works for Research mode and for prompts containing a URL.\n"
            "Disable in Settings > Internet Access to go fully offline."
        )
        self.lbl_web_badge.setStyleSheet(
            "color: #56b6c2; font-size: 11px; font-weight: bold; padding: 2px 8px; "
            "border: 1px solid #56b6c2; border-radius: 4px; background-color: #1a2830;"
        )
        self.lbl_web_badge.setVisible(False)
        ctrl_row.addWidget(self.lbl_web_badge)

        # ── Keep-Alive Connection Badge ────────────────────────────────
        self.lbl_connection = QLabel("⚪ Standby (loads on prompt)")
        self.lbl_connection.setToolTip(
            "Model stays loaded in RAM/VRAM during a 3-minute inactivity window for instant answers.\n"
            "Automatically unloads after 3m of inactivity to free memory. Window resets on any prompt."
        )
        self.lbl_connection.setStyleSheet(
            "color: #7f848e; font-size: 11px; font-weight: bold; padding: 2px 8px; "
            "border: 1px solid #3e4451; border-radius: 4px; background-color: #1e2227;"
        )
        ctrl_row.addWidget(self.lbl_connection)

        self.btn_warm = QPushButton("⚡ Preload")
        self.btn_warm.setToolTip("Preload and keep model open in memory for instant responses (5m window)")
        self.btn_warm.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_warm.clicked.connect(self._on_warm_clicked)
        ctrl_row.addWidget(self.btn_warm)

        ctrl_row.addStretch()

        self.lbl_token_meter = QLabel("Tokens: 0 / 8192")
        self.lbl_token_meter.setStyleSheet("color: #a0a0a0; font-size: 12px; font-weight: bold;")
        ctrl_row.addWidget(self.lbl_token_meter)
        main_layout.addLayout(ctrl_row)

        # ── Live Status / Thinking Indicator Row ──────────────────────
        self.status_container = QWidget()
        status_layout = QHBoxLayout(self.status_container)
        status_layout.setContentsMargins(0, 2, 0, 2)
        status_layout.setSpacing(8)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #61afef; font-size: 12px; font-weight: bold;")
        status_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background-color: #282c34; border: 1px solid #3e4451; border-radius: 4px; }"
            "QProgressBar::chunk { background-color: #98c379; border-radius: 3px; }"
        )
        status_layout.addWidget(self.progress_bar)

        self.status_container.setVisible(False)
        main_layout.addWidget(self.status_container)

        # 1-second countdown timer for inactivity window (default 3m = 180s, configurable)
        self._keep_alive_seconds = 180
        self._keep_alive_timer = QTimer(self)
        self._keep_alive_timer.setInterval(1000)
        self._keep_alive_timer.timeout.connect(self._on_timer_tick)

    def set_keep_alive_seconds(self, seconds: int) -> None:
        """Set idle keep_alive countdown duration in seconds."""
        self._keep_alive_seconds = max(10, int(seconds))

    def _on_model_text_changed(self, model: str) -> None:
        self.model_changed.emit(model)

    def _on_warm_clicked(self) -> None:
        model = self.current_model()
        if model:
            self.lbl_connection.setText("⏳ Preloading into memory…")
            self.lbl_connection.setStyleSheet(
                "color: #e5c07b; font-size: 11px; font-weight: bold; padding: 2px 8px; "
                "border: 1px solid #e5c07b; border-radius: 4px; background-color: #2c2820;"
            )
            self.keep_warm_requested.emit(model)

    def set_working(self) -> None:
        """Called when a prompt, plan, or build begins. Pauses idle countdown."""
        self._is_connected = True
        self._is_working = True
        self._keep_alive_timer.stop()
        self._seconds_remaining = self._keep_alive_seconds
        self.lbl_connection.setText("🟢 Active (working…)")
        self.lbl_connection.setToolTip("Model is actively executing a task. The idle countdown is paused.")
        self.lbl_connection.setStyleSheet(
            "color: #98c379; font-size: 11px; font-weight: bold; padding: 2px 8px; "
            "border: 1px solid #98c379; border-radius: 4px; background-color: #1e2820;"
        )

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
        self.lbl_connection.setText("⚪ Standby (loads on prompt)")
        self.lbl_connection.setToolTip(
            "Model will load into memory when a prompt is sent, and remain open for a 3-minute idle window."
        )
        self.lbl_connection.setStyleSheet(
            "color: #7f848e; font-size: 11px; font-weight: bold; padding: 2px 8px; "
            "border: 1px solid #3e4451; border-radius: 4px; background-color: #1e2227;"
        )

    def _on_timer_tick(self) -> None:
        if not self._is_connected or getattr(self, "_is_working", False):
            return
        self._seconds_remaining -= 1
        if self._seconds_remaining <= 0:
            self._keep_alive_timer.stop()
            self._is_connected = False
            self.lbl_connection.setText("⚪ Standby (loads on prompt)")
            self.lbl_connection.setStyleSheet(
                "color: #7f848e; font-size: 11px; font-weight: bold; padding: 2px 8px; "
                "border: 1px solid #3e4451; border-radius: 4px; background-color: #1e2227;"
            )
            self.connection_expired.emit(self.current_model())
        else:
            self._update_connection_badge()

    def _update_connection_badge(self) -> None:
        m = self._seconds_remaining // 60
        s = self._seconds_remaining % 60
        time_str = f"{m:02d}:{s:02d}"
        self.lbl_connection.setText(f"🟢 Connected (3m idle: {time_str})")
        self.lbl_connection.setToolTip(
            "Model is warm in memory. 3-minute idle window counting down. Automatically pauses when working."
        )
        self.lbl_connection.setStyleSheet(
            "color: #98c379; font-size: 11px; font-weight: bold; padding: 2px 8px; "
            "border: 1px solid #98c379; border-radius: 4px; background-color: #1e2820;"
        )

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
        color = "#e5c07b" if pct >= 0.70 else "#98c379"
        self.lbl_token_meter.setText(f"Tokens: ~{est:,} / {max_ctx:,}")
        self.lbl_token_meter.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")

    def show_status(self, text: str, progress: int | None = None) -> None:
        """Display live progress and status message."""
        self.lbl_status.setText(text)
        if progress is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, progress)))
        self.status_container.setVisible(True)

    def hide_status(self) -> None:
        self.status_container.setVisible(False)
        self.lbl_status.setText("")
