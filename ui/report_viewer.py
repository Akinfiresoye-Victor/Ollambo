"""
ui/report_viewer.py - Rich report viewer with QWebEngineView and fallback.

Features:
- Renders Markdown report using markdown-it-py (html=False, tables enabled)
- Converts ```mermaid blocks to <pre class="mermaid"> rendered by locally bundled assets/mermaid.min.js
- QWebEngineView created lazily upon opening, and completely destroyed on close (zero memory overhead when idle)
- Blocks internal navigation; opens external links in user's default browser
- Fallback to QTextBrowser if WebEngine is unavailable
- Actions: Copy Markdown, Export .md, Export HTML
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Optional WebEngine import
_HAS_WEBENGINE = False
try:
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE = True
except Exception:
    _HAS_WEBENGINE = False


def _get_mermaid_js_url() -> str:
    """Return local file:// URL for bundled assets/mermaid.min.js."""
    base_dir = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        else Path(__file__).resolve().parent.parent
    )
    js_path = base_dir / "assets" / "mermaid.min.js"
    return js_path.as_uri()


def markdown_to_report_html(markdown_text: str, mermaid_js_url: str = "") -> str:
    """
    Convert research markdown to sanitized HTML with Mermaid and tables.
    Raw HTML is strictly disabled (html=False) to prevent script injection.
    """
    import html as html_lib
    import markdown_it

    md = markdown_it.MarkdownIt("commonmark", {"html": False}).enable("table")
    raw_html = md.render(markdown_text)

    # Convert ```mermaid code blocks to <pre class="mermaid"> with unescaped arrows/quotes for Mermaid lexer
    def _clean_mermaid_block(m: re.Match) -> str:
        code = html_lib.unescape(m.group(1))
        # Strip any raw HTML tags inside mermaid diagram
        code = re.sub(r"<[^>]+>", "", code)
        return f'<pre class="mermaid">\n{code.strip()}\n</pre>'

    html_with_mermaid = re.sub(
        r'<pre><code class="language-mermaid">\s*(.*?)\s*</code></pre>',
        _clean_mermaid_block,
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Safely re-enable interactive <details> and <summary> for Quiz questions (keeps all other tags escaped)
    html_with_mermaid = re.sub(r"&lt;(/?)details&gt;", r"<\1details>", html_with_mermaid)
    html_with_mermaid = re.sub(r"&lt;(/?)summary&gt;", r"<\1summary>", html_with_mermaid)

    if not mermaid_js_url:
        mermaid_js_url = _get_mermaid_js_url()

    # Dark-themed stylesheet matching Ollambo's UI
    css = """
    body {
        background-color: #1e2227;
        color: #abb2bf;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        line-height: 1.6;
        padding: 30px;
        max-width: 900px;
        margin: 0 auto;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #e5c07b;
        margin-top: 1.4em;
        margin-bottom: 0.6em;
        border-bottom: 1px solid #282c34;
        padding-bottom: 0.3em;
    }
    h1 { color: #61afef; font-size: 2em; border-bottom: 2px solid #333842; }
    h2 { font-size: 1.4em; color: #98c379; }
    a { color: #61afef; text-decoration: none; }
    a:hover { text-decoration: underline; }
    code {
        background-color: #282c34;
        color: #e06c75;
        padding: 2px 6px;
        border-radius: 4px;
        font-family: Consolas, "Courier New", monospace;
        font-size: 0.9em;
    }
    pre {
        background-color: #21252b;
        border: 1px solid #2c313a;
        border-radius: 6px;
        padding: 12px;
        overflow-x: auto;
    }
    table {
        border-collapse: collapse;
        width: 100%;
        margin: 20px 0;
        font-size: 0.9em;
    }
    th, td {
        border: 1px solid #3e4451;
        padding: 10px 12px;
        text-align: left;
    }
    th {
        background-color: #282c34;
        color: #e5c07b;
        font-weight: 600;
    }
    tr:nth-child(even) { background-color: #21252b; }
    blockquote {
        margin: 15px 0;
        padding: 10px 20px;
        background-color: #21252b;
        border-left: 4px solid #61afef;
        color: #9da5b4;
    }
    .mermaid {
        background-color: #21252b;
        text-align: center;
        padding: 15px;
        border-radius: 6px;
        margin: 20px 0;
    }
    details {
        background-color: #21252b;
        border: 1px solid #2c313a;
        border-radius: 6px;
        padding: 10px 15px;
        margin: 15px 0;
    }
    summary {
        cursor: pointer;
        font-weight: bold;
        color: #61afef;
    }
    """

    full_page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{css}</style>
<script src="{mermaid_js_url}"></script>
<script>
document.addEventListener("DOMContentLoaded", function() {{
    if (window.mermaid) {{
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'dark',
            securityLevel: 'strict'
        }});
    }}
}});
</script>
</head>
<body>
{html_with_mermaid}
</body>
</html>
"""
    return full_page


if _HAS_WEBENGINE:
    class ExternalLinkPage(QWebEnginePage):
        """Custom web page that intercepts link navigation and opens external URLs in the browser."""

        def acceptNavigationRequest(self, url: QUrl, _nav_type: Any, _is_main_frame: bool) -> bool:
            scheme = url.scheme().lower()
            if scheme in ("http", "https"):
                QDesktopServices.openUrl(url)
                return False
            # Allow initial local load
            return True


class ReportViewerDialog(QDialog):
    """Lazy dialog displaying a research report."""

    def __init__(self, title: str, markdown_content: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Research Report: {title}")
        self.resize(1000, 780)
        self._markdown = markdown_content
        self._web_view: Any = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #1e2227; color: #abb2bf;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Top toolbar
        toolbar = QHBoxLayout()
        lbl_info = QLabel("📚 Research Report")
        lbl_info.setStyleSheet("font-size: 15px; font-weight: bold; color: #61afef;")
        toolbar.addWidget(lbl_info)

        toolbar.addStretch()

        btn_copy = QPushButton("📋 Copy Markdown")
        btn_copy.setStyleSheet("padding: 5px 12px; background-color: #282c34; border-radius: 4px;")
        btn_copy.clicked.connect(self._copy_markdown)
        toolbar.addWidget(btn_copy)

        btn_export_md = QPushButton("💾 Export .md")
        btn_export_md.setStyleSheet("padding: 5px 12px; background-color: #282c34; border-radius: 4px;")
        btn_export_md.clicked.connect(self._export_md)
        toolbar.addWidget(btn_export_md)

        btn_export_html = QPushButton("🌐 Export HTML")
        btn_export_html.setStyleSheet("padding: 5px 12px; background-color: #282c34; border-radius: 4px;")
        btn_export_html.clicked.connect(self._export_html)
        toolbar.addWidget(btn_export_html)

        btn_close = QPushButton("✕ Close")
        btn_close.setStyleSheet("padding: 5px 12px; background-color: #4b5263; color: #ffffff; border-radius: 4px;")
        btn_close.clicked.connect(self.close)
        toolbar.addWidget(btn_close)

        layout.addLayout(toolbar)

        # Secondary toolbar: find in report and zoom
        sub_bar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Find in report… (Press Enter)")
        self.search_input.setMaximumWidth(220)
        self.search_input.setStyleSheet(
            "QLineEdit { background-color: #21252b; color: #abb2bf; border: 1px solid #333842; "
            "border-radius: 4px; padding: 4px 8px; }"
        )
        self.search_input.returnPressed.connect(self._find_next)
        sub_bar.addWidget(self.search_input)

        btn_find = QPushButton("Find Next")
        btn_find.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        btn_find.clicked.connect(self._find_next)
        sub_bar.addWidget(btn_find)

        sub_bar.addStretch()

        btn_zoom_out = QPushButton("A-")
        btn_zoom_out.setToolTip("Zoom Out")
        btn_zoom_out.setStyleSheet("padding: 2px 8px; font-weight: bold;")
        btn_zoom_out.clicked.connect(self._zoom_out)
        sub_bar.addWidget(btn_zoom_out)

        btn_zoom_in = QPushButton("A+")
        btn_zoom_in.setToolTip("Zoom In")
        btn_zoom_in.setStyleSheet("padding: 2px 8px; font-weight: bold;")
        btn_zoom_in.clicked.connect(self._zoom_in)
        sub_bar.addWidget(btn_zoom_in)

        layout.addLayout(sub_bar)

        # Content view: WebEngine if available, fallback to QTextBrowser
        html_doc = markdown_to_report_html(self._markdown)

        if _HAS_WEBENGINE:
            try:
                self._web_view = QWebEngineView(self)
                page = ExternalLinkPage(self._web_view)
                page.settings().setAttribute(page.settings().WebAttribute.LocalContentCanAccessFileUrls, True)
                page.settings().setAttribute(page.settings().WebAttribute.JavascriptEnabled, True)
                self._web_view.setPage(page)

                base_dir = (
                    Path(sys._MEIPASS)
                    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
                    else Path(__file__).resolve().parent.parent
                ) / "assets"
                base_url = QUrl.fromLocalFile(str(base_dir) + os.sep)
                self._web_view.setHtml(html_doc, baseUrl=base_url)
                layout.addWidget(self._web_view)
                return
            except Exception:
                self._web_view = None

        # Fallback view
        fallback_notice = QLabel("ℹ️ WebEngine view unavailable. Showing read-only formatted text with copyable Mermaid blocks.")
        fallback_notice.setStyleSheet("color: #e5c07b; font-size: 12px; padding: 4px;")
        layout.addWidget(fallback_notice)

        tb = QTextBrowser(self)
        tb.setStyleSheet("background-color: #21252b; color: #dcdcdc; border: 1px solid #333842; font-size: 13px;")
        tb.setOpenExternalLinks(True)
        tb.setMarkdown(self._markdown)
        layout.addWidget(tb)

    def _find_next(self) -> None:
        q = self.search_input.text().strip()
        if not q:
            return
        if self._web_view is not None:
            self._web_view.findText(q)

    def _zoom_in(self) -> None:
        if self._web_view is not None:
            self._web_view.setZoomFactor(min(3.0, self._web_view.zoomFactor() + 0.15))

    def _zoom_out(self) -> None:
        if self._web_view is not None:
            self._web_view.setZoomFactor(max(0.5, self._web_view.zoomFactor() - 0.15))

    def _copy_markdown(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self._markdown)
            QMessageBox.information(self, "Copied", "Markdown report copied to clipboard.")

    def _export_md(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Markdown", "report.md", "Markdown Files (*.md)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._markdown)
                QMessageBox.information(self, "Exported", f"Saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file: {e}")

    def _export_html(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export HTML", "report.html", "HTML Files (*.html)")
        if path:
            try:
                html_doc = markdown_to_report_html(self._markdown)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html_doc)
                QMessageBox.information(self, "Exported", f"Saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file: {e}")

    def closeEvent(self, event) -> None:
        """Destroy WebEngine resources immediately on close to free memory."""
        if self._web_view is not None:
            try:
                self._web_view.setPage(None)
                self._web_view.deleteLater()
                self._web_view = None
            except Exception:
                pass
        super().closeEvent(event)
