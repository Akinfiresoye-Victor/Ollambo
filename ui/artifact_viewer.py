from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import workspace
from ui.artifacts_panel import ArtifactsPanel
from ui.icons import icon
from ui.theme.tokens import DARK


class _Scrim(QWidget):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ArtifactViewer(QWidget):
    """Resizable in-window overlay for browsing a workspace's files."""

    closed = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._project_name = ""
        self._project_path = ""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        host.installEventFilter(self)
        self.installEventFilter(self)

        self.scrim = _Scrim(self)
        self.scrim.setStyleSheet("background-color: rgba(0, 0, 0, 160);")
        self.scrim.clicked.connect(self.close_viewer)

        self.panel = QFrame(self)
        self.panel.setObjectName("ArtifactViewerPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.panel.setStyleSheet(
            f"QFrame#ArtifactViewerPanel {{ background-color: {DARK.colors.surface}; "
            f"border: 1px solid {DARK.colors.border}; border-radius: 16px; }}"
        )
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.name_label = QLabel()
        self.name_label.setProperty("role", "title")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        header.addWidget(self.name_label)
        self.path_label = QLabel()
        self.path_label.setProperty("role", "caption")
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        header.addWidget(self.path_label, 1)
        self.panel_content = ArtifactsPanel(self.panel)
        self._add_action(header, "copy", "Copy code", self.panel_content._copy_current_code)
        self._add_action(header, "download", "Save file", self.panel_content._save_single_file)
        self._add_action(header, "download", "Save all files", self.panel_content._save_all_files)
        self._add_action(header, "download", "Export zip", self.panel_content._export_project_zip)
        self._add_action(header, "folder", "Open folder", self.panel_content._open_project_folder)
        self._add_action(header, "x", "Close viewer", self.close_viewer)
        layout.addLayout(header)

        self.panel_content.prepare_for_viewer("")
        layout.addWidget(self.panel_content, 1)
        self.resize_overlay()

    def _add_action(self, layout: QHBoxLayout, icon_name: str, tooltip: str, callback) -> None:
        button = QToolButton(self.panel)
        button.setIcon(icon(icon_name))
        button.setToolTip(tooltip)
        button.setProperty("variant", "ghost")
        button.setProperty("iconOnly", "true")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(32, 32)
        button.clicked.connect(callback)
        layout.addWidget(button)

    def open_workspace(self, workspace_id: str) -> None:
        project = workspace.get_workspace(workspace_id)
        if project is None:
            self.close_viewer()
            return
        path = workspace.workspace_path(workspace_id)
        self._project_name = str(project.get("name") or "Project")
        self._project_path = str(path or project.get("slug") or "")
        self.name_label.setText(self._project_name)
        self.name_label.setToolTip(self._project_name)
        self.path_label.setToolTip(self._project_path)
        self._update_path_label()
        self.panel_content.load_workspace(workspace_id)
        self.panel_content.prepare_for_viewer(self._project_name)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.resize_overlay()

    def resize_overlay(self) -> None:
        self.setGeometry(self._host.rect())
        self.scrim.setGeometry(self.rect())
        width = max(1, int(self.width() * 0.88))
        height = max(1, int(self.height() * 0.88))
        self.panel.setGeometry(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )
        self._update_path_label()

    def _update_path_label(self) -> None:
        if not hasattr(self, "path_label"):
            return
        width = max(0, self.path_label.width() - 4)
        self.path_label.setText(
            QFontMetrics(self.path_label.font()).elidedText(
                self._project_path, Qt.TextElideMode.ElideMiddle, width
            )
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self.resize_overlay()
        elif event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_viewer()
            event.accept()
            return
        super().keyPressEvent(event)

    def close_viewer(self) -> None:
        self.hide()
        self._host.removeEventFilter(self)
        self.closed.emit()
        self.deleteLater()
