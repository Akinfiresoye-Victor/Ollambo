from __future__ import annotations

from math import ceil

from PySide6.QtCore import QSize, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTextBrowser


class AutoTextView(QTextBrowser):
    """Read-only rich text pane that expands to its content without stealing scroll."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "body")
        self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._updating_height = False
        self._last_viewport_width = 0
        self.anchorClicked.connect(self._on_anchor_clicked)
        self.document().documentLayout().documentSizeChanged.connect(self._update_height)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(ceil(self.document().idealWidth()), hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def _update_height(self, *args) -> None:
        if self._updating_height:
            return
        width = self.viewport().width()
        if width < 50:
            return
        self._updating_height = True
        try:
            doc = self.document()
            doc.setTextWidth(width)
            self.setFixedHeight(ceil(doc.size().height()) + 2)
        finally:
            self._updating_height = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.viewport().width()
        if width != self._last_viewport_width:
            self._last_viewport_width = width
            self._update_height()

    def wheelEvent(self, event) -> None:
        event.ignore()
