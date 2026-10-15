"""
Widget representing a single chat message (User or Assistant).

Features:
  - High-performance, zero-flicker streaming: updates text and code blocks in-place
    without deleting and re-creating child widgets during generation.
  - Preserves scroll stability: no layout collapses or disappearing text when scrolling.
  - Rich Markdown formatting for text blocks (headers, bold, italics, lists, quotes, links).
  - Monospace code containers with language tags, line-fitting, and instant 'Copy' button.
"""

from __future__ import annotations

import re
from math import ceil

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.markdown_formatter import markdown_to_html
from ui.icons import icon
from ui.theme import set_variant
from ui.text_utils import strip_emoji_prefix
from ui.widgets.auto_text_view import AutoTextView


class ThinkingIndicator(QLabel):
    """Small animated three-dot indicator used while the assistant is producing text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dots = ["", ".", "..", "..."]
        self._index = 0
        self.setProperty("role", "muted")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._tick()

    def _tick(self) -> None:
        self.setText(f"Thinking{self._dots[self._index]}")
        self._index = (self._index + 1) % len(self._dots)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(250)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)


class CodeBlockWidget(QFrame):
    """Monospace code container with a top header and Copy button."""

    def __init__(self, code: str, language: str = "", parent=None) -> None:
        super().__init__(parent)
        self.code = code
        self.language = language
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setProperty("role", "card")
        self.setObjectName("CodeBlock")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        lang_display = language.strip() or "code"
        self.lang_label = QLabel(lang_display)
        self.lang_label.setProperty("role", "accent")
        header.addWidget(self.lang_label)
        header.addStretch()

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedWidth(72)
        self.copy_btn.setIcon(icon("copy"))
        self.copy_btn.setToolTip("Copy code")
        set_variant(self.copy_btn, "ghost")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        header.addWidget(self.copy_btn)
        layout.addLayout(header)

        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlainText(code)
        self.edit.setProperty("role", "code")
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.wheelEvent = lambda event: event.ignore() if event is not None else None

        self._update_editor_height(code)
        layout.addWidget(self.edit)

    def _update_editor_height(self, code: str) -> None:
        line_count = max(1, code.count("\n") + 1)
        font_metrics = self.edit.fontMetrics()
        line_height = font_metrics.lineSpacing()
        ideal_height = min(420, (line_count * line_height) + 24)
        self.edit.setFixedHeight(max(55, ideal_height))

    def update_code(self, new_code: str, language: str = "") -> None:
        """Update code content in-place without rebuilding widget."""
        if new_code != self.code:
            self.code = new_code
            self.edit.setPlainText(new_code)
            self._update_editor_height(new_code)
        if language and language != self.language:
            self.language = language
            self.lang_label.setText(language)

    def _copy_to_clipboard(self) -> None:
        """Copies code text to system clipboard and provides feedback."""
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self.code)
            self.copy_btn.setIcon(icon("check"))
            self.copy_btn.setText("Copied")
            QTimer.singleShot(1500, lambda: (self.copy_btn.setIcon(icon("copy")), self.copy_btn.setText("Copy")))


_CODE_BLOCK_PATTERN = re.compile(r"```([a-zA-Z0-9_\-\+]*)\n?(.*?)```", re.DOTALL)
_UNCLOSED_FENCE_PATTERN = re.compile(r"```([a-zA-Z0-9_\-\+]*)\n?(.*)$", re.DOTALL)


class ChatMessageWidget(QFrame):
    """Displays a single user or assistant chat message with formatted Markdown and stable streaming."""

    def __init__(
        self,
        role: str,
        content: str = "",
        attachments: list[dict] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self._content = content
        self._attachments = attachments or []
        self._blocks: list[dict] = []
        self._placeholder: QLabel | None = None
        self._parsed_raw = ""
        self._closed_segments: list[dict] = []
        self._closed_idx = 0

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        is_user = role == "user"
        self.setProperty("messageRole", role)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(6)

        self._content_container = QWidget()
        self._content_container.setSizePolicy(
            QSizePolicy.Policy.Preferred if is_user else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._content_layout = QVBoxLayout(self._content_container)
        if is_user:
            self._content_layout.setContentsMargins(14, 10, 14, 10)
        else:
            self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(4)
        self._user_bubble = is_user

        if not is_user:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)
            from core.assets import get_app_pixmap
            pix = get_app_pixmap(18)
            if not pix.isNull():
                av = QLabel()
                av.setPixmap(pix)
                av.setFixedSize(18, 18)
                av.setAlignment(Qt.AlignmentFlag.AlignTop)
                row.addWidget(av, 0, Qt.AlignmentFlag.AlignTop)
            self._content_container.setMaximumWidth(760)
            row.addWidget(self._content_container, 1)
            self._layout.addLayout(row)
        else:
            self._content_container.setObjectName("UserMessageBubble")
            self._content_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self._content_container.setMinimumWidth(0)
            self._content_container.setMaximumWidth(608)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch()
            row.addWidget(
                self._content_container,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            )
            self._layout.addLayout(row)

        self.set_content(content, is_final=bool(content))
        self._update_user_bubble_width()

        # Attachment chips if present
        if self._attachments:
            att_row = QHBoxLayout()
            att_row.setSpacing(6)
            for att in self._attachments:
                name = att.get("name", "Project")
                icon_label = QLabel()
                icon_label.setPixmap(icon("file", size=16).pixmap(16, 16))
                att_row.addWidget(icon_label)
                chip = QLabel(name)
                chip.setProperty("role", "attachment")
                att_row.addWidget(chip)
            att_row.addStretch()
            self._layout.addLayout(att_row)

    def get_content(self) -> str:
        return self._content

    def set_content(self, new_content: str, is_final: bool = False) -> None:
        """Update content in-place with zero widget flickering."""
        # Skip re-render if content unchanged (common during rapid flush ticks)
        if (
            new_content == self._content
            and not is_final
            and (new_content.strip() or self._placeholder is not None)
        ):
            return
        self._content = new_content

        if not new_content.strip():
            if not self._placeholder:
                self._clear_all_blocks()
                self._placeholder = ThinkingIndicator()
                self._placeholder.setFixedHeight(20)
                self._content_layout.addWidget(self._placeholder)
                self.updateGeometry()
            return

        if self._placeholder:
            self._content_layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None

        display_content = new_content
        if self.role == "assistant":
            cleaned_content, _ = strip_emoji_prefix(new_content)
            if cleaned_content.startswith(("Plan Ready", "Build")):
                display_content = cleaned_content
        segments = self._parse_segments(display_content)
        self._sync_segments_to_ui(segments, is_final=is_final)

        self._update_user_bubble_width()
        self.updateGeometry()

    def _update_user_bubble_width(self) -> None:
        if not self._user_bubble:
            return
        column_width = min(self.width(), 760)
        if column_width <= 0:
            return

        max_width = int(column_width * 0.8)
        content_width = 0
        for block in self._blocks:
            widget = block["widget"]
            if isinstance(widget, AutoTextView):
                doc = widget.document()
                doc.setTextWidth(-1)
                content_width = max(content_width, ceil(doc.idealWidth()))
            else:
                content_width = max(content_width, widget.sizeHint().width())

        margins = self._content_layout.contentsMargins()
        horizontal_padding = margins.left() + margins.right() + 2
        self._content_container.setMaximumWidth(
            min(max_width, content_width + horizontal_padding)
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_user_bubble_width()

    def _parse_segments(self, text: str) -> list[dict]:
        """Parse raw markdown into segments, caching completed blocks to avoid regex over unchanged text."""
        # Reset cache if text does not extend the previously parsed closed prefix
        if not text.startswith(self._parsed_raw[:self._closed_idx]):
            self._closed_segments.clear()
            self._closed_idx = 0

        self._parsed_raw = text

        # Only scan from the last unclosed point if fences might be present
        if "```" in text[self._closed_idx:]:
            last_idx = self._closed_idx
            for match in _CODE_BLOCK_PATTERN.finditer(text, self._closed_idx):
                start, end = match.span()
                pre_text = text[last_idx:start]
                if pre_text.strip():
                    self._closed_segments.append({"type": "text", "content": pre_text.strip()})

                lang = match.group(1).strip()
                code = match.group(2)
                self._closed_segments.append({"type": "code", "lang": lang, "content": code})
                last_idx = end
                self._closed_idx = end

        # Parse remaining tail
        tail = text[self._closed_idx:]
        tail_segments: list[dict] = []
        if tail:
            if "```" in tail:
                unclosed_match = _UNCLOSED_FENCE_PATTERN.search(tail)
                if unclosed_match:
                    pre_code = tail[: unclosed_match.start()].strip()
                    if pre_code:
                        tail_segments.append({"type": "text", "content": pre_code})
                    lang = unclosed_match.group(1).strip()
                    code = unclosed_match.group(2)
                    tail_segments.append({"type": "code", "lang": lang, "content": code})
                elif tail.strip():
                    tail_segments.append({"type": "text", "content": tail.strip()})
            elif tail.strip():
                tail_segments.append({"type": "text", "content": tail.strip()})

        return list(self._closed_segments) + tail_segments

    def _sync_segments_to_ui(self, segments: list[dict], is_final: bool = False) -> None:
        """Sync segments to widgets, updating only the last changing block during streaming."""
        # Align widget list size and types
        for i, seg in enumerate(segments):
            if i < len(self._blocks):
                curr = self._blocks[i]
                if curr["type"] == seg["type"]:
                    # Type matches, update content only if this is the last block or if final
                    if is_final or i == len(segments) - 1:
                        if curr["type"] == "text":
                            if curr["content"] != seg["content"] or is_final:
                                curr["content"] = seg["content"]
                                if is_final:
                                    curr["widget"].setHtml(markdown_to_html(seg["content"]))
                                else:
                                    curr["widget"].setPlainText(seg["content"])
                                curr["widget"].update()
                        else:  # code
                            curr["content"] = seg["content"]
                            curr["lang"] = seg.get("lang", "")
                            curr["widget"].update_code(seg["content"], seg.get("lang", ""))
                    continue
                else:
                    # Type changed -> replace widget at position i
                    self._content_layout.removeWidget(curr["widget"])
                    curr["widget"].deleteLater()
                    new_w = self._create_segment_widget(seg, is_final=is_final)
                    self._content_layout.insertWidget(i, new_w)
                    self._blocks[i] = {"type": seg["type"], "widget": new_w, "content": seg["content"]}
                    if seg["type"] == "code":
                        self._blocks[i]["lang"] = seg.get("lang", "")
                    continue

            # Append new block
            new_w = self._create_segment_widget(seg, is_final=is_final)
            self._content_layout.addWidget(new_w)
            block_data = {"type": seg["type"], "widget": new_w, "content": seg["content"]}
            if seg["type"] == "code":
                block_data["lang"] = seg.get("lang", "")
            self._blocks.append(block_data)

        # Remove extra blocks if text shortened
        while len(self._blocks) > len(segments):
            extra = self._blocks.pop()
            self._content_layout.removeWidget(extra["widget"])
            extra["widget"].deleteLater()

        # If final, ensure all earlier text blocks receive full markdown formatting
        if is_final:
            for b in self._blocks:
                if b["type"] == "text":
                    b["widget"].setHtml(markdown_to_html(b["content"]))
                    b["widget"].update()

    def _create_segment_widget(self, seg: dict, is_final: bool = True) -> QWidget:
        """Create rich-text content or a code block widget."""
        if seg["type"] == "text":
            view = AutoTextView()
            view.setProperty("role", "body")
            if is_final:
                view.setHtml(markdown_to_html(seg["content"]))
            else:
                view.setPlainText(seg["content"])
            view._update_height()
            return view
        else:
            return CodeBlockWidget(seg["content"], language=seg.get("lang", ""))

    def _clear_all_blocks(self) -> None:
        """Remove all block widgets cleanly."""
        for b in self._blocks:
            self._content_layout.removeWidget(b["widget"])
            b["widget"].deleteLater()
        self._blocks.clear()
        self._parsed_raw = ""
        self._closed_segments.clear()
        self._closed_idx = 0
