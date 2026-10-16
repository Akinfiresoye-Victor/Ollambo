from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from core import workspace
from ui.icons import Spinner, icon
from ui.theme.tokens import DARK
from ui.widgets.toast import show_toast


class ArtifactCard(QFrame):
    """Compact project card for opening and exporting a workspace."""

    open_requested = Signal(str)

    def __init__(self, project: dict, busy: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.workspace_id = str(project.get("id") or project.get("slug") or "")
        self._name = str(project.get("name") or "Untitled project")
        self.setObjectName("ArtifactCard")
        self.setProperty("role", "card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(104)
        self._build_ui(busy)

    def _build_ui(self, busy: bool) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(12)

        folder = QLabel()
        folder.setPixmap(icon("folder", DARK.colors.accent, 22).pixmap(22, 22))
        folder.setFixedSize(24, 24)
        outer.addWidget(folder, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.name_label = QLabel(self._name)
        self.name_label.setProperty("role", "title")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.name_label.setToolTip(self._name)
        title_row.addWidget(self.name_label, 1)
        if busy:
            self.spinner = Spinner(16, parent=self)
            title_row.addWidget(self.spinner)
        else:
            self.spinner = None
        goal = str(self.project.get("goal") or "").replace("\n", " ").strip()
        self._goal = goal
        self.goal_label = QLabel(goal)
        self.goal_label.setProperty("role", "muted")
        self.goal_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.goal_label.setToolTip(goal)
        caption = QLabel(
            f"{self._file_count()} files, updated {self._updated_ago(self.project.get('updated'))}"
        )
        caption.setProperty("role", "caption")
        body.addLayout(title_row)
        body.addWidget(self.goal_label)
        body.addWidget(caption)
        outer.addLayout(body, 1)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self.open_folder_button = self._action_button("folder", "Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.export_button = self._action_button("download", "Export zip")
        self.export_button.clicked.connect(self._export_zip)
        actions.addWidget(self.open_folder_button)
        actions.addWidget(self.export_button)
        outer.addLayout(actions)

    def _action_button(self, icon_name: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon(icon_name))
        button.setToolTip(tooltip)
        button.setProperty("variant", "ghost")
        button.setProperty("iconOnly", "true")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(28, 28)
        return button

    def _file_count(self) -> int:
        if self.workspace_id:
            return len(workspace.list_files(self.workspace_id))
        return int(self.project.get("file_count") or 0)

    @staticmethod
    def _updated_ago(value: object) -> str:
        try:
            if isinstance(value, (int, float)):
                updated = datetime.fromtimestamp(float(value), tz=timezone.utc)
            else:
                updated = datetime.fromisoformat(str(value))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                else:
                    updated = updated.astimezone(timezone.utc)
            seconds = max(0, int((datetime.now(timezone.utc) - updated).total_seconds()))
        except (OverflowError, OSError, TypeError, ValueError):
            return "recently"
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    def enterEvent(self, event) -> None:
        self.setStyleSheet(
            f"QFrame#ArtifactCard {{ background-color: {DARK.colors.raised}; "
            f"border: 1px solid {DARK.colors.border}; border-radius: {DARK.radii.card}px; }}"
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setStyleSheet("")
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self.workspace_id)
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = max(0, self.name_label.width() - 4)
        self.name_label.setText(
            QFontMetrics(self.name_label.font()).elidedText(
                self._name, Qt.TextElideMode.ElideRight, width
            )
        )
        goal_width = max(0, self.goal_label.width() - 4)
        self.goal_label.setText(
            QFontMetrics(self.goal_label.font()).elidedText(
                self._goal, Qt.TextElideMode.ElideRight, goal_width
            )
        )

    def _open_folder(self) -> None:
        path = workspace.workspace_path(self.workspace_id)
        if path is None:
            QMessageBox.warning(self, "Project unavailable", "The project folder could not be found.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _export_zip(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export project",
            f"{self.project.get('slug') or self.workspace_id}.zip",
            "ZIP archives (*.zip)",
        )
        if not path:
            return
        if not workspace.export_zip(self.workspace_id, Path(path)):
            QMessageBox.critical(self, "Export failed", "The project could not be exported.")
            return
        show_toast(self, "Project exported")
