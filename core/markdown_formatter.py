"""
High-performance Markdown to Styled Rich HTML formatter for dark-theme chat UI.

Converts markdown blocks (excluding fenced code blocks) into styled, readable HTML:
  - Headings (#, ##, ###, ####) with hierarchical sizing & theme colors
  - Bold (**text** or __text__)
  - Italic (*text* or _text_)
  - Inline code (`code`) with dark badge background and monospace font
  - Bullet lists (-, *, +) and numbered lists (1., 2., etc.)
  - Blockquotes (> text) with accent left border
  - Horizontal rules (---, ***)
  - Markdown links ([text](url)) with external browser link support
  - Clean paragraph spacing and typography

Performance: All regex patterns are pre-compiled at module level.
"""

from __future__ import annotations

import html
import re
from functools import lru_cache

_HR_PATTERN = re.compile(r"^(\-{3,}|\*{3,}|_{3,})$")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_PATTERN = re.compile(r"^[\-\*\+]\s+(.*)$")
_OL_PATTERN = re.compile(r"^(\d+)\.\s+(.*)$")
_QUOTE_PATTERN = re.compile(r"^>\s*(.*)$")

# Pre-compiled inline formatting patterns (avoids re.compile on every token flush)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD_STAR = re.compile(r"\*\*([^*]+)\*\*")
_BOLD_UNDER = re.compile(r"(?<!\w)__(?!\w+__(?!\w))([^_]+)__(?!\w)")
_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_ITALIC_UNDER = re.compile(r"(?<!\w)_([^_]+)_(?!\w)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Pre-built replacement strings
_BOLD_REPL = r"<b style='color: #e5c07b; font-weight: bold;'>\1</b>"
_ITALIC_STAR_REPL = r"<i style='color: #d19a66;'>\1</i>"
_ITALIC_UNDER_REPL = r"<i style='color: #d19a66;'>\1</i>"


def _format_inline(raw_text: str) -> str:
    """Escapes HTML and applies markdown inline styles (code, bold, italic, links)."""
    # 1. Escape HTML special characters
    safe = html.escape(raw_text, quote=False)

    # 2. Protect inline code spans so formatting never runs inside them
    code_spans: list[str] = []

    def save_code(m: re.Match) -> str:
        idx = len(code_spans)
        span_html = (
            f"<span style='background-color: #282c34; color: #98c379; padding: 1px 5px; "
            f"border: 1px solid #333842; border-radius: 3px; font-family: Consolas, monospace; "
            f"font-size: 12px;'>{m.group(1)}</span>"
        )
        code_spans.append(span_html)
        return f"\x00INLINE_CODE_{idx}\x00"

    safe = _INLINE_CODE.sub(save_code, safe)

    # 3. Bold: **text** or __text__
    safe = _BOLD_STAR.sub(_BOLD_REPL, safe)
    safe = _BOLD_UNDER.sub(_BOLD_REPL, safe)

    # 4. Italic: *text* or _text_ (underscores only at word boundaries)
    safe = _ITALIC_STAR.sub(_ITALIC_STAR_REPL, safe)
    safe = _ITALIC_UNDER.sub(_ITALIC_UNDER_REPL, safe)

    # 5. Markdown Links: [text](url) - only allow http and https
    def replace_link(m: re.Match) -> str:
        link_text = m.group(1)
        target = m.group(2).strip()
        if re.match(r"^https?://", target, re.IGNORECASE):
            return f"<a href='{target}' style='color: #61afef; text-decoration: underline; font-weight: bold;'>{link_text}</a>"
        return m.group(0)

    safe = _LINK.sub(replace_link, safe)

    # 6. Restore protected inline code spans
    for idx, span_html in enumerate(code_spans):
        safe = safe.replace(f"\x00INLINE_CODE_{idx}\x00", span_html)

    return safe


def markdown_to_html(md_text: str) -> str:
    """Transform markdown text into neatly formatted, styled HTML for dark mode."""
    if not md_text or not md_text.strip():
        return ""

    lines = md_text.splitlines()
    html_lines: list[str] = []
    in_ul = False
    in_ol = False

    def close_lists() -> str:
        nonlocal in_ul, in_ol
        res = ""
        if in_ul:
            res += "</ul>"
            in_ul = False
        if in_ol:
            res += "</ol>"
            in_ol = False
        return res

    for raw_line in lines:
        line = raw_line.strip()

        # Empty line -> paragraph break
        if not line:
            closed = close_lists()
            html_lines.append(f"{closed}<div style='height: 6px;'></div>")
            continue

        # Horizontal Rule
        if _HR_PATTERN.match(line):
            closed = close_lists()
            html_lines.append(f"{closed}<hr style='border: none; border-top: 1px solid #3e4451; margin: 8px 0;'>")
            continue

        # Headings
        h_match = _HEADING_PATTERN.match(line)
        if h_match:
            closed = close_lists()
            level = len(h_match.group(1))
            heading_text = _format_inline(h_match.group(2))
            if level == 1:
                html_lines.append(f"{closed}<h1 style='color: #61afef; font-size: 16px; font-weight: bold; margin: 10px 0 4px 0;'>{heading_text}</h1>")
            elif level == 2:
                html_lines.append(f"{closed}<h2 style='color: #61afef; font-size: 15px; font-weight: bold; margin: 8px 0 3px 0;'>{heading_text}</h2>")
            elif level == 3:
                html_lines.append(f"{closed}<h3 style='color: #98c379; font-size: 14px; font-weight: bold; margin: 6px 0 2px 0;'>{heading_text}</h3>")
            else:
                html_lines.append(f"{closed}<h4 style='color: #e5c07b; font-size: 13px; font-weight: bold; margin: 4px 0 2px 0;'>{heading_text}</h4>")
            continue

        # Blockquote
        q_match = _QUOTE_PATTERN.match(line)
        if q_match:
            closed = close_lists()
            q_text = _format_inline(q_match.group(1))
            html_lines.append(
                f"{closed}<div style='border-left: 3px solid #61afef; margin: 4px 0 4px 6px; padding: 2px 0 2px 10px; color: #abb2bf; font-style: italic;'>{q_text}</div>"
            )
            continue

        # Unordered list: -, *, +
        ul_match = _UL_PATTERN.match(line)
        if ul_match:
            item_text = _format_inline(ul_match.group(1))
            if not in_ul:
                closed = close_lists()
                html_lines.append(f"{closed}<ul style='margin: 3px 0 3px 0; padding-left: 18px;'>")
                in_ul = True
            html_lines.append(f"<li style='margin: 2px 0; color: #abb2bf; line-height: 1.45;'>{item_text}</li>")
            continue

        # Ordered list: 1., 2., etc.
        ol_match = _OL_PATTERN.match(line)
        if ol_match:
            item_text = _format_inline(ol_match.group(2))
            if not in_ol:
                closed = close_lists()
                html_lines.append(f"{closed}<ol style='margin: 3px 0 3px 0; padding-left: 18px;'>")
                in_ol = True
            html_lines.append(f"<li style='margin: 2px 0; color: #abb2bf; line-height: 1.45;'>{item_text}</li>")
            continue

        # Normal paragraph text
        closed = close_lists()
        p_text = _format_inline(line)
        html_lines.append(f"{closed}<p style='margin: 3px 0; line-height: 1.5; color: #abb2bf; font-size: 13px;'>{p_text}</p>")

    html_lines.append(close_lists())
    return "".join(html_lines)
