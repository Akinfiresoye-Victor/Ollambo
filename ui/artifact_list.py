from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLineEdit,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.artifact_card import ArtifactCard
from ui.icons import icon


class ArtifactListPanel(QWidget):
    """Scrollable project-card list shared by chat inspector and Library."""

    open_requested = Signal(str)

    def __init__(
        self,
        empty_message: str,
        filterable: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._projects: list[dict] = []
        self._busy_workspace_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.filter_input: QLineEdit | None = None
        if filterable:
            self.filter_input = QLineEdit()
            self.filter_input.setPlaceholderText("Filter projects")
            self.filter_input.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
            self.filter_input.textChanged.connect(self._render)
            layout.addWidget(self.filter_input)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.container = QWidget()
        self.cards_layout = QVBoxLayout(self.container)
        self.cards_layout.setContentsMargins(2, 2, 2, 2)
        self.cards_layout.setSpacing(8)
        self.empty_label = QLabel(empty_message)
        self.empty_label.setProperty("role", "muted")
        self.empty_label.setContentsMargins(8, 18, 8, 18)
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

    def set_projects(
        self,
        projects: list[dict],
        busy_workspace_id: str | None = None,
    ) -> None:
        self._projects = list(projects)
        self._busy_workspace_id = busy_workspace_id
        self._render()

    def _render(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                widget.deleteLater()

        query = self.filter_input.text().strip().casefold() if self.filter_input else ""
        visible = [
            project
            for project in self._projects
            if not query
            or query in str(project.get("name", "")).casefold()
            or query in str(project.get("goal", "")).casefold()
        ]
        if not visible:
            self.empty_label.show()
            self.empty_label.setText(
                "No artifacts in this chat yet."
                if not self._projects
                else "No projects match this filter."
            )
            self.cards_layout.addWidget(self.empty_label)
        else:
            self.empty_label.hide()
            for project in visible:
                project_id = str(project.get("id") or project.get("slug") or "")
                card = ArtifactCard(
                    project,
                    busy=project_id == self._busy_workspace_id,
                    parent=self.container,
                )
                card.open_requested.connect(self.open_requested.emit)
                self.cards_layout.addWidget(card)
        self.cards_layout.addStretch()
