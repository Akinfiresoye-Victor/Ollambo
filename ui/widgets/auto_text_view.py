from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTextBrowser


class AutoTextView(QTextBrowser):
    """Read-only rich text pane that expands to its content without stealing scroll."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.anchorClicked.connect(self._on_anchor_clicked)
        self.document().documentLayout().documentSizeChanged.connect(self._update_height)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    def _update_height(self, *args) -> None:
        height = max(18, int(self.document().size().height()) + 10)
        self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_height()

    def wheelEvent(self, event) -> None:
        event.ignore()
