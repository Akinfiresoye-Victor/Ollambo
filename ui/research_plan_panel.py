"""
ui/research_plan_panel.py - Plan review panel for deep research.

Displays the drafted research plan:
- Main research question
- Skeptic checklist (myths, biases)
- Editable list of sub-questions (edit text, add new, remove selected)
- Buttons: "Approve and Start Research" and "Cancel"

Matches the visual style of plan_panel.py without modifying plan_panel.py.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.research_planner import ResearchPlan, SubQuestion


class ResearchPlanPanel(QFrame):
    """Panel showing the drafted research plan with interactive review controls."""

    # Emitted when user approves the plan: passes updated ResearchPlan
    plan_approved = Signal(object)

    # Emitted when user cancels
    plan_cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_plan: ResearchPlan | None = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "ResearchPlanPanel { background-color: #21252b; border: 1px solid #333842; border-radius: 6px; padding: 6px; }"
        )
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Header Title
        title = QLabel("🔬 Proposed Research Plan")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #61afef;")
        layout.addWidget(title)

        # Main Question
        self.lbl_main = QLabel("Main Question: -")
        self.lbl_main.setWordWrap(True)
        self.lbl_main.setStyleSheet("font-size: 12px; font-weight: bold; color: #abb2bf;")
        layout.addWidget(self.lbl_main)

        # Sub-questions label & edit toolbar
        sq_header_row = QHBoxLayout()
        lbl_sq = QLabel("Sub-Questions to Investigate:")
        lbl_sq.setStyleSheet("font-size: 12px; font-weight: bold; color: #e5c07b;")
        sq_header_row.addWidget(lbl_sq)

        sq_header_row.addStretch()

        self.btn_add_sq = QPushButton("+ Add")
        self.btn_add_sq.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_add_sq.clicked.connect(self._on_add_sq)
        sq_header_row.addWidget(self.btn_add_sq)

        self.btn_del_sq = QPushButton("- Remove")
        self.btn_del_sq.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_del_sq.clicked.connect(self._on_del_sq)
        sq_header_row.addWidget(self.btn_del_sq)

        layout.addLayout(sq_header_row)

        # Editable Sub-Questions List
        self.list_sq = QListWidget()
        self.list_sq.setStyleSheet(
            "QListWidget { background-color: #1e2227; color: #dcdcdc; border: 1px solid #282c34; border-radius: 4px; }"
            "QListWidget::item { padding: 4px; }"
        )
        self.list_sq.itemDoubleClicked.connect(self._on_edit_sq)
        layout.addWidget(self.list_sq)

        # Skeptic Checklist summary box
        self.skeptic_box = QTextEdit()
        self.skeptic_box.setReadOnly(True)
        self.skeptic_box.setMaximumHeight(70)
        self.skeptic_box.setStyleSheet(
            "background-color: #1b1d23; color: #9da5b4; border: 1px solid #282c34; font-size: 11px;"
        )
        layout.addWidget(self.skeptic_box)

        # Action Buttons row
        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("padding: 6px 14px;")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch()

        self.btn_approve = QPushButton("Approve & Start Research")
        self.btn_approve.setStyleSheet(
            "QPushButton { background-color: #98c379; color: #1e1e1e; font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #87b368; }"
        )
        self.btn_approve.clicked.connect(self._on_approve)
        btn_row.addWidget(self.btn_approve)
        layout.addLayout(btn_row)

    def set_plan(self, plan: ResearchPlan) -> None:
        """Display the plan in the panel."""
        self._current_plan = plan
        self.lbl_main.setText(f"Core Question: {plan.main_question}")

        self.list_sq.clear()
        for sq in plan.sub_questions:
            item = QListWidgetItem(f"SQ {sq.index}: {sq.question}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.list_sq.addItem(item)

        # Populate skeptic checklist
        chk = plan.skeptic_checklist or {}
        myths = chk.get("myths", [])
        biases = chk.get("biases", [])
        summary_lines = ["🛡️ Skeptic Checklist:"]
        if myths:
            summary_lines.append("• Myths to test: " + ", ".join(myths))
        if biases:
            summary_lines.append("• Biases checked: " + ", ".join(biases))
        if len(summary_lines) == 1:
            summary_lines.append("• Standard adversarial cross-examination enabled.")
        self.skeptic_box.setText("\n".join(summary_lines))

    def _on_add_sq(self) -> None:
        if not self._current_plan:
            return
        text, ok = QInputDialog.getText(self, "Add Sub-Question", "Enter sub-question:")
        if ok and text.strip():
            idx = len(self._current_plan.sub_questions) + 1
            new_sq = SubQuestion(
                index=idx,
                question=text.strip(),
                why_it_matters="User added sub-question",
                search_queries=[text.strip()],
                evidence_for="Relevant evidence",
                evidence_against="Counter evidence",
                preferred_sources=["authoritative sources"],
            )
            self._current_plan.sub_questions.append(new_sq)
            item = QListWidgetItem(f"SQ {idx}: {text.strip()}")
            self.list_sq.addItem(item)

    def _on_del_sq(self) -> None:
        if not self._current_plan:
            return
        row = self.list_sq.currentRow()
        if 0 <= row < len(self._current_plan.sub_questions):
            self._current_plan.sub_questions.pop(row)
            # Reindex
            for i, sq in enumerate(self._current_plan.sub_questions, start=1):
                sq.index = i
            self.set_plan(self._current_plan)

    def _on_edit_sq(self, item: QListWidgetItem) -> None:
        if not self._current_plan:
            return
        row = self.list_sq.row(item)
        if 0 <= row < len(self._current_plan.sub_questions):
            curr_text = self._current_plan.sub_questions[row].question
            text, ok = QInputDialog.getText(self, "Edit Sub-Question", "Edit sub-question:", text=curr_text)
            if ok and text.strip():
                self._current_plan.sub_questions[row].question = text.strip()
                item.setText(f"SQ {row + 1}: {text.strip()}")

    def _on_approve(self) -> None:
        if self._current_plan:
            self.plan_approved.emit(self._current_plan)

    def _on_cancel(self) -> None:
        self.plan_cancelled.emit()
