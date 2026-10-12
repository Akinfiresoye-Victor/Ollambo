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
        self.setStyleSheet(
            "PlanPanel { background-color: #21252b; border: 1px solid #333842; border-radius: 6px; padding: 6px; }"
        )
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Header Title
        title = QLabel("Proposed Coding Plan")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #61afef;")
        layout.addWidget(title)

        # Goal Label
        self.lbl_goal = QLabel("Goal: -")
        self.lbl_goal.setWordWrap(True)
        self.lbl_goal.setStyleSheet("font-size: 12px; color: #abb2bf;")
        layout.addWidget(self.lbl_goal)

        # Checklist of steps
        lbl_steps = QLabel("Steps to Execute:")
        lbl_steps.setStyleSheet("font-size: 12px; font-weight: bold; color: #e5c07b;")
        layout.addWidget(lbl_steps)

        self.list_steps = QListWidget()
        self.list_steps.setStyleSheet(
            "QListWidget { background-color: #1e2227; color: #dcdcdc; border: 1px solid #282c34; border-radius: 4px; }"
            "QListWidget::item { padding: 4px; }"
        )
        layout.addWidget(self.list_steps)

        # Raw notes / assumptions box (collapsible or small)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Assumptions or notes…")
        self.notes_edit.setMaximumHeight(60)
        self.notes_edit.setStyleSheet(
            "background-color: #1b1d23; color: #888888; border: 1px solid #282c34; font-size: 11px;"
        )
        layout.addWidget(self.notes_edit)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_reject = QPushButton("Reject / Edit")
        self.btn_reject.clicked.connect(self._on_reject)
        btn_row.addWidget(self.btn_reject)

        btn_row.addStretch()

        self.btn_approve = QPushButton("Approve & Start Coding")
        self.btn_approve.setStyleSheet(
            "QPushButton { background-color: #98c379; color: #1e1e1e; font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #87b368; }"
        )
        self.btn_approve.clicked.connect(self._on_approve)
        btn_row.addWidget(self.btn_approve)
        layout.addLayout(btn_row)

    def set_plan(self, plan: CodingPlan) -> None:
        """Display a new plan in the panel."""
        self._current_plan = plan
        self.lbl_goal.setText(f"Goal: {plan.goal}")

        # Set assumptions text
        if plan.assumptions:
            self.notes_edit.setPlainText("Assumptions:\n• " + "\n• ".join(plan.assumptions))
        else:
            self.notes_edit.setPlainText(plan.raw_text or "No specific assumptions.")

        # Populate checklist
        self.list_steps.clear()
        for s in plan.steps:
            text = f"Step {s.step}: {s.description}  →  `{s.file}`"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if s.completed else Qt.CheckState.Unchecked)
            self.list_steps.addItem(item)

        self.setVisible(True)

    def mark_step_completed(self, step_number: int) -> None:
        """Mark a step as done in the checklist."""
        for i in range(self.list_steps.count()):
            item = self.list_steps.item(i)
            if item.text().startswith(f"Step {step_number}:"):
                item.setCheckState(Qt.CheckState.Checked)
                item.setForeground(Qt.GlobalColor.darkGreen)

    def _on_approve(self) -> None:
        if self._current_plan:
            self.btn_approve.setEnabled(False)
            self.plan_approved.emit(self._current_plan)

    def _on_reject(self) -> None:
        self.setVisible(False)
        self.plan_rejected.emit()
