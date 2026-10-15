"""
First-run setup wizard.

Shows three checks (installed → server → models), offers auto-install or
manual steps, and lets the user pick & pull a model.  When all checks pass,
emits setup_complete so the main window can switch to the chat page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.installer import InstallPlan, get_install_plan
from core.setup_checker import SUGGESTED_MODELS, check_hardware_capabilities
from ui.icons import icon
from ui.text_utils import strip_emoji_prefix
from ui.terminal_widget import TerminalWidget
from ui.theme.tokens import DARK
from ui.widgets.toast import show_toast
from ui.worker_guard import request_stop, wait_briefly, worker_is_running
from workers.install_worker import InstallWorker
from workers.pull_worker import PullWorker
from workers.setup_worker import SetupWorker

class SetupWizard(QWidget):
    """Step-by-step setup checks with install/pull support."""

    # Emitted when all checks pass.
    #   (installed_models: list[str], server_started_by_us: bool)
    setup_complete = Signal(list, bool)

    def __init__(self, ollama_url: str, setup_done: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._ollama_url = ollama_url
        self._setup_done = setup_done
        self._setup_worker: SetupWorker | None = None
        self._install_worker: InstallWorker | None = None
        self._pull_worker: PullWorker | None = None
        self._server_started_by_us = False

        self._build_ui()

    # ── UI layout ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        from core.assets import get_app_pixmap
        if not (pix := get_app_pixmap(44)).isNull():
            lbl = QLabel()
            lbl.setPixmap(pix)
            hdr.addWidget(lbl)
        t_box = QVBoxLayout()
        t_box.setSpacing(2)
        title = QLabel("Setting up Ollambo")
        title.setProperty("role", "title")
        subtitle = QLabel("Checking your system…")
        subtitle.setProperty("role", "muted")
        t_box.addWidget(title)
        t_box.addWidget(subtitle)
        hdr.addLayout(t_box)
        hdr.addStretch()
        layout.addLayout(hdr)

        # ── Check status lines ────────────────────────────────────────
        self._check_icons: dict[str, QLabel] = {}
        self._check_labels = {
            "installed": QLabel("Ollama installed — checking…"),
            "server": QLabel("Ollama server — waiting…"),
            "models": QLabel("Models available — waiting…"),
        }
        self._check_states: dict[str, str] = {}
        for name, lbl in self._check_labels.items():
            row = QHBoxLayout()
            icon_label = QLabel()
            icon_label.setFixedSize(18, 18)
            row.addWidget(icon_label)
            lbl.setProperty("role", "body")
            row.addWidget(lbl)
            row.addStretch()
            layout.addLayout(row)
            self._check_icons[name] = icon_label
            self._set_check_status(name, "checking", lbl.text())
        self._lbl_installed = self._check_labels["installed"]
        self._lbl_server = self._check_labels["server"]
        self._lbl_models = self._check_labels["models"]

        # ── Install section (hidden by default) ───────────────────────
        self._install_section = QWidget()
        install_layout = QVBoxLayout(self._install_section)
        install_layout.setContentsMargins(0, 8, 0, 0)

        self._manual_steps = QTextEdit()
        self._manual_steps.setReadOnly(True)
        self._manual_steps.setMaximumHeight(120)
        install_layout.addWidget(self._manual_steps)

        self._btn_install = QPushButton("Install Ollama for me")
        self._btn_install.clicked.connect(self._on_install_clicked)
        install_layout.addWidget(self._btn_install)

        self._install_section.setVisible(False)
        layout.addWidget(self._install_section)

        # ── Terminal (for install output) ─────────────────────────────
        self._terminal = TerminalWidget()
        self._terminal.setMaximumHeight(160)
        self._terminal.setVisible(False)
        layout.addWidget(self._terminal)

        # ── Model picker section (hidden by default) ──────────────────
        self._model_section = QWidget()
        model_layout = QVBoxLayout(self._model_section)
        model_layout.setContentsMargins(0, 8, 0, 0)
        model_layout.addWidget(QLabel("Pick a model to download:"))

        self._model_combo = QComboBox()
        for m in SUGGESTED_MODELS:
            self._model_combo.addItem(m["desc"], m["name"])
        model_layout.addWidget(self._model_combo)

        # Hardware check recommendation for first run
        hw = check_hardware_capabilities()
        if hw["suggest_low_power"]:
            rec_idx = self._model_combo.findData(hw["recommended_model"])
            if rec_idx >= 0:
                self._model_combo.setCurrentIndex(rec_idx)
            hw_label = QLabel(
                f"System check: {hw['total_ram_gb']} GB RAM detected (≤8 GB).\n"
                f"Suggested: Low-Power Mode and lightweight model '{hw['recommended_model']}'."
            )
            hw_label.setProperty("role", "warning")
            hw_icon = QLabel()
            hw_icon.setPixmap(icon("cpu", size=16).pixmap(16, 16))
            model_layout.addWidget(hw_icon)
            model_layout.addWidget(hw_label)

        self._low_power_check = QCheckBox("Enable Low-Power Mode (optimizes CPU, RAM & VRAM)")
        self._low_power_check.setIcon(icon("cpu"))
        self._low_power_check.setChecked(hw["suggest_low_power"])
        model_layout.addWidget(self._low_power_check)

        self._btn_pull = QPushButton("Download selected model")
        self._btn_pull.clicked.connect(self._on_pull_clicked)
        model_layout.addWidget(self._btn_pull)

        self._pull_status, self._pull_progress = QLabel(""), QProgressBar()
        self._pull_progress.setRange(0, 100)
        self._pull_progress.setVisible(False)
        model_layout.addWidget(self._pull_status)
        model_layout.addWidget(self._pull_progress)

        self._model_section.setVisible(False)
        layout.addWidget(self._model_section)

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_recheck = QPushButton("Re-check")
        self._btn_recheck.clicked.connect(self.run_checks)
        self._btn_continue = QPushButton("Continue →")
        self._btn_continue.setEnabled(False)
        self._btn_continue.clicked.connect(self._on_continue)
        btn_row.addWidget(self._btn_recheck)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_continue)
        layout.addLayout(btn_row)
        layout.addStretch()

    # ── Run checks ────────────────────────────────────────────────────

    def run_checks(self) -> None:
        """Start (or restart) the setup checks."""
        # Reset UI.
        self._set_check_status("installed", "checking", "Ollama installed — checking…")
        self._set_check_status("server", "checking", "Ollama server — waiting…")
        self._set_check_status("models", "checking", "Models available — waiting…")
        self._install_section.setVisible(False)
        self._model_section.setVisible(False)
        self._btn_continue.setEnabled(False)

        # Stop any running worker.
        if worker_is_running(self._setup_worker):
            request_stop(self._setup_worker)
            wait_briefly(self._setup_worker, 2000)

        self._setup_worker = SetupWorker(self._ollama_url, parent=self)
        self._setup_worker.check_result.connect(self._on_check_result)
        self._setup_worker.all_done.connect(self._on_all_done)
        self._setup_worker.start()

    # ── Slots: check results ──────────────────────────────────────────

    @Slot(str, bool, str)
    def _on_check_result(self, name: str, ok: bool, detail: str) -> None:
        if name in self._check_labels:
            state = "ok" if ok else "failed"
            clean_detail, _ = strip_emoji_prefix(detail)
            self._set_check_status(name, state, f"{name.replace('_', ' ').title()} — {clean_detail}")

    def _set_check_status(self, name: str, state: str, text: str) -> None:
        label = self._check_labels.get(name)
        icon_label = self._check_icons.get(name)
        if label is None or icon_label is None:
            return
        self._check_states[name] = state
        clean_text, _ = strip_emoji_prefix(text)
        label.setText(clean_text)
        icon_name = {"checking": "loader", "ok": "check", "failed": "alert-triangle"}[state]
        color = DARK.colors.accent if state == "ok" else DARK.colors.text_secondary
        icon_label.setPixmap(icon(icon_name, color, 16).pixmap(16, 16))

    @Slot(bool, list, bool)
    def _on_all_done(self, all_ok: bool, models: list[str], server_started: bool) -> None:
        if server_started:
            self._server_started_by_us = True
        self._discovered_models = models

        if all_ok:
            self._btn_continue.setEnabled(True)
            self._install_section.setVisible(False)
            self._model_section.setVisible(False)
            if getattr(self, "_setup_done", False):
                self._on_continue()
        else:
            # Figure out what failed and show the right action section.
            if self._check_states.get("installed") == "failed":
                self._show_install_section()
            elif self._check_states.get("models") == "failed":
                self._model_section.setVisible(True)

    # ── Install flow ──────────────────────────────────────────────────

    def _show_install_section(self) -> None:
        plan = get_install_plan()
        self._current_plan = plan

        if plan.can_auto_install:
            self._manual_steps.setPlainText(
                f"Command to run:\n  {plan.display_command}\n\n"
                f"If this fails, here are manual steps:\n{plan.manual_steps}"
            )
            self._btn_install.setVisible(True)
            self._btn_install.setText(f'Run "{plan.display_command}"')
        else:
            self._manual_steps.setPlainText(plan.manual_steps)
            self._btn_install.setVisible(False)

        self._install_section.setVisible(True)

    def _on_install_clicked(self) -> None:
        """User clicked the install button — confirm first, then run."""
        plan = self._current_plan
        reply = QMessageBox.question(
            self,
            "Confirm install",
            f"Ollambo will run this command:\n\n  {plan.display_command}\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._terminal.clear_output()
        self._terminal.setVisible(True)
        self._btn_install.setEnabled(False)

        self._install_worker = InstallWorker(plan, parent=self)
        self._install_worker.output_line.connect(self._terminal.append_text)
        self._install_worker.finished_install.connect(self._on_install_finished)
        self._install_worker.start()

    @Slot(bool, str)
    def _on_install_finished(self, success: bool, message: str) -> None:
        self._btn_install.setEnabled(True)
        self._terminal.append_text(f"\n{message}\n")
        if success:
            show_toast(self, "Ollama installed successfully")
            # Re-run checks to verify install worked.
            self.run_checks()

    # ── Model pull flow ───────────────────────────────────────────────

    def _on_pull_clicked(self) -> None:
        model = self._model_combo.currentData()
        if not model:
            return

        self._btn_pull.setEnabled(False)
        self._pull_progress.setVisible(True)
        self._pull_progress.setValue(0)
        self._pull_status.setText(f"Downloading {model}…")

        self._pull_worker = PullWorker(model, self._ollama_url, parent=self)
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.finished_pull.connect(self._on_pull_finished)
        self._pull_worker.start()

    @Slot(str, int, int)
    def _on_pull_progress(self, status: str, completed: int, total: int) -> None:
        if total > 0:
            pct = int(completed * 100 / total)
            self._pull_progress.setValue(pct)
        clean_status, _ = strip_emoji_prefix(status)
        self._pull_status.setText(clean_status)

    @Slot(bool, str)
    def _on_pull_finished(self, success: bool, message: str) -> None:
        self._btn_pull.setEnabled(True)
        clean_message, _ = strip_emoji_prefix(message)
        self._pull_status.setText(clean_message)
        self._pull_progress.setVisible(False)
        if success:
            show_toast(self, f"Model ready: {self._model_combo.currentText()}")
            # Re-run checks — this time models should be found.
            self.run_checks()

    # ── Continue ──────────────────────────────────────────────────────

    def _on_continue(self) -> None:
        models = getattr(self, "_discovered_models", [])
        if hasattr(self, "_low_power_check"):
            from core.storage import load_settings, save_settings
            st = load_settings()
            st["low_power_mode"] = self._low_power_check.isChecked()
            save_settings(st)
        self.setup_complete.emit(models, self._server_started_by_us)

    # ── Cleanup ───────────────────────────────────────────────────────

    def stop_workers(self) -> None:
        """Stop all running workers (called on app exit)."""
        for w in (self._setup_worker, self._install_worker, self._pull_worker):
            if worker_is_running(w):
                request_stop(w)
                wait_briefly(w, 2000)
