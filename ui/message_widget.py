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
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication
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


class CodeBlockWidget(QFrame):
    """Monospace code container with a top header and Copy button."""

    def __init__(self, code: str, language: str = "", parent=None) -> None:
        super().__init__(parent)
        self.code = code
        self.language = language
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "CodeBlockWidget {"
            "  background-color: #1e1e1e;"
            "  border: 1px solid #333333;"
            "  border-radius: 5px;"
            "  margin: 6px 0px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)

        # Header bar (Language label + Copy button)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        lang_display = language.strip() or "code"
        self.lang_label = QLabel(lang_display)
        self.lang_label.setStyleSheet("color: #61afef; font-size: 11px; font-weight: bold; font-family: Consolas;")
        header.addWidget(self.lang_label)
        header.addStretch()

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedWidth(64)
        self.copy_btn.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 3px 8px; background: #282c34; color: #abb2bf; "
            "border: 1px solid #3e4451; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background: #3e4451; color: #ffffff; }"
        )
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        header.addWidget(self.copy_btn)
        layout.addLayout(header)

        # Code editor area
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlainText(code)
        self.edit.setFont(QFont("Consolas", 10))
        self.edit.setStyleSheet(
            "QPlainTextEdit {"
            "  background-color: #14161a;"
            "  color: #abb2bf;"
            "  border: 1px solid #21252b;"
            "  border-radius: 3px;"
            "  selection-background-color: #264f78;"
            "  font-family: Consolas, monospace;"
            "}"
        )

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
            self.copy_btn.setText("Copied!")
            QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy"))


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

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        is_user = role == "user"
        bg_color = "#24272e" if is_user else "#1e2227"
        border_color = "#3b4048" if is_user else "#2c313a"
        self.setStyleSheet(
            f"ChatMessageWidget {{"
            f"  background-color: {bg_color};"
            f"  border: 1px solid {border_color};"
            f"  border-radius: 6px;"
            f"  margin: 4px 0px;"
            f"}}"
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(6)

        # Header with role title and Ollambo face for assistant
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(6)
        if not is_user:
            from core.assets import get_app_pixmap
            pix = get_app_pixmap(18)
            if not pix.isNull():
                av = QLabel()
                av.setPixmap(pix)
                hdr_row.addWidget(av)
        sender_name = "You" if is_user else "Ollambo"
        title = QLabel(sender_name)
        title.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {'#61afef' if is_user else '#98c379'};"
        )
        hdr_row.addWidget(title)
        hdr_row.addStretch()
        self._layout.addLayout(hdr_row)

        # Content container
        self._content_container = QWidget()
        self._content_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._content_layout = QVBoxLayout(self._content_container)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(4)
        self._layout.addWidget(self._content_container)

        self.set_content(content, is_final=bool(content))

        # Attachment chips if present
        if self._attachments:
            att_row = QHBoxLayout()
            att_row.setSpacing(6)
            for att in self._attachments:
                name = att.get("name", "Project")
                chip = QLabel(f"📎 {name}")
                chip.setStyleSheet(
                    "background-color: #2c313a; color: #61afef; border: 1px solid #3e4451; "
                    "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: bold;"
                )
                att_row.addWidget(chip)
            att_row.addStretch()
            self._layout.addLayout(att_row)

    def get_content(self) -> str:
        return self._content

    def set_content(self, new_content: str, is_final: bool = False) -> None:
        """Update content in-place with zero widget flickering."""
        # Skip re-render if content unchanged (common during rapid flush ticks)
        if new_content == self._content and not is_final:
            return
        self._content = new_content

        if not new_content.strip():
            if not self._placeholder:
                self._clear_all_blocks()
                self._placeholder = QLabel("Thinking…")
                self._placeholder.setStyleSheet("color: #777777; font-style: italic; font-size: 13px;")
                self._content_layout.addWidget(self._placeholder)
            return

        if self._placeholder:
            self._content_layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None

        segments = self._parse_segments(new_content)
        self._sync_segments_to_ui(segments, is_final=is_final)

        self.updateGeometry()

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
                                    curr["widget"].setTextFormat(Qt.TextFormat.RichText)
                                    curr["widget"].setText(markdown_to_html(seg["content"]))
                                else:
                                    curr["widget"].setTextFormat(Qt.TextFormat.PlainText)
                                    curr["widget"].setText(seg["content"])
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
                    b["widget"].setTextFormat(Qt.TextFormat.RichText)
                    b["widget"].setText(markdown_to_html(b["content"]))
                    b["widget"].update()

    def _create_segment_widget(self, seg: dict, is_final: bool = True) -> QWidget:
        """Create a QLabel for text or a CodeBlockWidget for code."""
        if seg["type"] == "text":
            lbl = QLabel()
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            lbl.setWordWrap(True)
            lbl.setOpenExternalLinks(True)
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            lbl.setStyleSheet("color: #abb2bf; font-size: 13px; line-height: 1.45;")
            if is_final:
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setText(markdown_to_html(seg["content"]))
            else:
                lbl.setTextFormat(Qt.TextFormat.PlainText)
                lbl.setText(seg["content"])
            return lbl
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
