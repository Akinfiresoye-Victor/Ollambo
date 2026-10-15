"""
Plan Panel for reviewing, editing, and approving structured coding plans.

Displays the model's plan (Goal, Assumptions, Files, Steps) as an interactive
checklist. Lets the user approve the plan to trigger code generation or cancel.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.planner import CodingPlan
from ui.theme import set_variant


class PlanPanel(QFrame):
    """Panel showing the structured coding plan and approval controls."""

    # Emitted when the user approves the plan: passes CodingPlan
    plan_approved = Signal(object)

    # Emitted when the user cancels/rejects the plan
    plan_rejected = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_plan: CodingPlan | None = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("Card")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Header Title
        title = QLabel("Proposed Coding Plan")
        title.setProperty("role", "title")
        layout.addWidget(title)

        # Goal Label
        self.lbl_goal = QLabel("Goal: -")
        self.lbl_goal.setWordWrap(True)
        self.lbl_goal.setProperty("role", "muted")
        layout.addWidget(self.lbl_goal)

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(0, 12, 0, 0)
        self.empty_label = QLabel("No plan yet. Ask for a project in Code mode.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "muted")
        empty_layout.addWidget(self.empty_label)
        layout.addWidget(self.empty_state)

        # Checklist of steps
        lbl_steps = QLabel("Steps to Execute:")
        lbl_steps.setProperty("role", "section")
        layout.addWidget(lbl_steps)

        self.list_steps = QListWidget()
        self.list_steps.setWordWrap(True)
        self.list_steps.setAlternatingRowColors(False)
        self.list_steps.setSpacing(2)
        self.list_steps.setStyleSheet("QListWidget::item { margin: 2px 0; }")
        layout.addWidget(self.list_steps)

        # Raw notes / assumptions box (collapsible or small)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Assumptions or notes…")
        self.notes_edit.setMaximumHeight(60)
        self.notes_edit.setProperty("role", "muted")
        layout.addWidget(self.notes_edit)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_reject = QPushButton("Reject / Edit")
        self.btn_reject.clicked.connect(self._on_reject)
        btn_row.addWidget(self.btn_reject)

        btn_row.addStretch()

        self.btn_approve = QPushButton("Approve & Start Coding")
        set_variant(self.btn_approve, "primary")
        self.btn_approve.clicked.connect(self._on_approve)
        btn_row.addWidget(self.btn_approve)
        layout.addLayout(btn_row)

        self._show_empty_state()

    def _show_empty_state(self) -> None:
        self.empty_state.setVisible(True)
        self.list_steps.setVisible(False)
        self.notes_edit.setVisible(False)
        self.btn_reject.setVisible(False)
        self.btn_approve.setVisible(False)
        self._current_plan = None

    def set_plan(self, plan: CodingPlan) -> None:
        """Display a new plan in the panel."""
        self._current_plan = plan
        self.empty_state.setVisible(False)
        self.list_steps.setVisible(True)
        self.notes_edit.setVisible(True)
        self.btn_reject.setVisible(True)
        self.btn_approve.setVisible(True)
        self.lbl_goal.setText(f"Goal: {plan.goal}")

        # Set assumptions text
        if plan.assumptions:
            self.notes_edit.setPlainText("Assumptions:\n• " + "\n• ".join(plan.assumptions))
        else:
            self.notes_edit.setPlainText(plan.raw_text or "No specific assumptions.")

        # Populate checklist
        self.list_steps.clear()
        for s in plan.steps:
            text = f"Step {s.step}: {s.description} → {s.file}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if s.completed else Qt.CheckState.Unchecked)
            item.setSizeHint(self.list_steps.sizeHint())
            self.list_steps.addItem(item)

    def mark_step_completed(self, step_number: int) -> None:
        """Mark a step as done in the checklist."""
        for i in range(self.list_steps.count()):
            item = self.list_steps.item(i)
            if item.text().startswith(f"Step {step_number}:"):
                item.setCheckState(Qt.CheckState.Checked)

    def _on_approve(self) -> None:
        if self._current_plan:
            self.btn_approve.setEnabled(False)
            self.plan_approved.emit(self._current_plan)

    def _on_reject(self) -> None:
        self._show_empty_state()
        self.plan_rejected.emit()
