"""
Settings dialog for configuring Ollama connection and model defaults.

Configures:
  - Ollama REST API URL (default: http://localhost:11434)
  - Default chat model
  - Summarizer model (for Phase 3 context compaction)
  - Context window size (num_ctx, default: 8192)
"""

from __future__ import annotations

from typing import Any
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.storage import save_settings


class SettingsDialog(QDialog):
    """Modal dialog allowing the user to view and adjust settings."""

    def __init__(
        self,
        settings: dict[str, Any],
        available_models: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ollambo Settings")
        self.resize(480, 290)
        from core.assets import get_app_icon
        if not (icon := get_app_icon()).isNull():
            self.setWindowIcon(icon)
        self._settings = settings
        self._models = available_models

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # 1. Ollama URL
        self.url_edit = QLineEdit(self._settings.get("ollama_url", "http://localhost:11434"))
        form.addRow("Ollama URL:", self.url_edit)

        # 2. Default model
        self.default_model_combo = QComboBox()
        self.default_model_combo.addItem("(None)", "")
        for m in self._models:
            self.default_model_combo.addItem(m, m)
        current_default = self._settings.get("default_model", "")
        idx = self.default_model_combo.findData(current_default)
        if idx >= 0:
            self.default_model_combo.setCurrentIndex(idx)
        form.addRow("Default Model:", self.default_model_combo)

        # 3. Summarizer model (for Phase 3)
        self.summarizer_combo = QComboBox()
        self.summarizer_combo.addItem("(Same as chat model)", "")
        for m in self._models:
            self.summarizer_combo.addItem(m, m)
        current_summ = self._settings.get("summarizer_model", "")
        idx = self.summarizer_combo.findData(current_summ)
        if idx >= 0:
            self.summarizer_combo.setCurrentIndex(idx)
        form.addRow("Summarizer Model:", self.summarizer_combo)

        # 4. Context size (num_ctx)
        self.num_ctx_spin = QSpinBox()
        self.num_ctx_spin.setRange(1024, 131072)
        self.num_ctx_spin.setSingleStep(1024)
        self.num_ctx_spin.setValue(int(self._settings.get("num_ctx", 8192)))
        form.addRow("Context Size (num_ctx):", self.num_ctx_spin)

        # 5. Parallel sub-agents
        self.max_agents_spin = QSpinBox()
        self.max_agents_spin.setRange(1, 6)
        self.max_agents_spin.setValue(int(self._settings.get("max_agents", 2)))
        form.addRow("Parallel Sub-Agents:", self.max_agents_spin)

        # 6. Automated testing & self-healing
        self.auto_test_check = QCheckBox("Automatically test code and auto-fix bugs")
        self.auto_test_check.setChecked(bool(self._settings.get("auto_test", True)))
        form.addRow("Self-Healing Tests:", self.auto_test_check)

        layout.addLayout(form)
        layout.addSpacing(10)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("font-weight: bold;")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _on_save(self) -> None:
        """Saves values into settings dict and persists to disk."""
        self._settings["ollama_url"] = self.url_edit.text().strip().rstrip("/")
        self._settings["default_model"] = self.default_model_combo.currentData()
        self._settings["summarizer_model"] = self.summarizer_combo.currentData()
        self._settings["num_ctx"] = self.num_ctx_spin.value()
        self._settings["max_agents"] = self.max_agents_spin.value()
        self._settings["auto_test"] = self.auto_test_check.isChecked()

        save_settings(self._settings)
        self.accept()
