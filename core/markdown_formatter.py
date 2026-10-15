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

_PALETTE = {
    "primary": (229, 192, 123),
    "secondary": (171, 178, 191),
    "tertiary": (157, 165, 180),
    "accent": (97, 175, 239),
    "bold": (229, 192, 123),
    "italic": (209, 154, 102),
    "heading_subtle": (152, 195, 121),
    "code_background": (40, 44, 52),
    "code_text": (152, 195, 121),
    "border": (62, 68, 81),
    "raised": (51, 56, 66),
}
_MONO_FONT = "Consolas, monospace"


def set_palette(
    *,
    primary: tuple[int, int, int] = (229, 192, 123),
    secondary: tuple[int, int, int] = (171, 178, 191),
    tertiary: tuple[int, int, int] = (157, 165, 180),
    accent: tuple[int, int, int] = (97, 175, 239),
    bold: tuple[int, int, int] = (229, 192, 123),
    italic: tuple[int, int, int] = (209, 154, 102),
    heading_subtle: tuple[int, int, int] = (152, 195, 121),
    code_background: tuple[int, int, int] = (40, 44, 52),
    code_text: tuple[int, int, int] = (152, 195, 121),
    border: tuple[int, int, int] = (62, 68, 81),
    raised: tuple[int, int, int] = (51, 56, 66),
    mono_font: str = "Consolas, monospace",
) -> None:
    """Set rich-text colors without making the core depend on the UI theme."""
    global _MONO_FONT
    _PALETTE.update(
        primary=primary,
        secondary=secondary,
        tertiary=tertiary,
        accent=accent,
        bold=bold,
        italic=italic,
        heading_subtle=heading_subtle,
        code_background=code_background,
        code_text=code_text,
        border=border,
        raised=raised,
    )
    _MONO_FONT = mono_font


def _css_color(name: str) -> str:
    red, green, blue = _PALETTE[name]
    return f"rgb({red}, {green}, {blue})"


def _format_inline(raw_text: str) -> str:
    """Escapes HTML and applies markdown inline styles (code, bold, italic, links)."""
    # 1. Escape HTML special characters
    safe = html.escape(raw_text, quote=False)

    # 2. Protect inline code spans so formatting never runs inside them
    code_spans: list[str] = []

    def save_code(m: re.Match) -> str:
        idx = len(code_spans)
        span_html = (
            f"<span style='background-color: {_css_color('code_background')}; "
            f"color: {_css_color('code_text')}; padding: 1px 5px; "
            f"border: 1px solid {_css_color('raised')}; border-radius: 3px; "
            f"font-family: {_MONO_FONT}; "
            f"font-size: 12px;'>{m.group(1)}</span>"
        )
        code_spans.append(span_html)
        return f"\x00INLINE_CODE_{idx}\x00"

    safe = _INLINE_CODE.sub(save_code, safe)

    # 3. Bold: **text** or __text__
    bold = f"<b style='color: {_css_color('bold')}; font-weight: bold;'>\\1</b>"
    safe = _BOLD_STAR.sub(bold, safe)
    safe = _BOLD_UNDER.sub(bold, safe)

    # 4. Italic: *text* or _text_ (underscores only at word boundaries)
    italic = f"<i style='color: {_css_color('italic')};'>\\1</i>"
    safe = _ITALIC_STAR.sub(italic, safe)
    safe = _ITALIC_UNDER.sub(italic, safe)

    # 5. Markdown Links: [text](url) - only allow http and https
    def replace_link(m: re.Match) -> str:
        link_text = m.group(1)
        target = m.group(2).strip()
        if re.match(r"^https?://", target, re.IGNORECASE):
            return (
                f"<a href='{target}' style='color: {_css_color('accent')}; "
                f"text-decoration: underline; font-weight: bold;'>{link_text}</a>"
            )
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
    last_block_was_paragraph = False
    pending_paragraph_gap = False

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
            if not in_ul and not in_ol and last_block_was_paragraph:
                pending_paragraph_gap = True
            closed = close_lists()
            if closed:
                html_lines.append(closed)
                last_block_was_paragraph = False
            continue

        is_paragraph = not (
            _HR_PATTERN.match(line)
            or _HEADING_PATTERN.match(line)
            or _QUOTE_PATTERN.match(line)
            or _UL_PATTERN.match(line)
            or _OL_PATTERN.match(line)
        )
        if pending_paragraph_gap:
            if last_block_was_paragraph and is_paragraph:
                html_lines.append("<div style='height: 4px;'></div>")
            pending_paragraph_gap = False

        # Horizontal Rule
        if _HR_PATTERN.match(line):
            closed = close_lists()
            html_lines.append(
                f"{closed}<hr style='border: none; border-top: 1px solid "
                f"{_css_color('border')}; margin: 8px 0;'>"
            )
            last_block_was_paragraph = False
            continue

        # Headings
        h_match = _HEADING_PATTERN.match(line)
        if h_match:
            closed = close_lists()
            level = len(h_match.group(1))
            heading_text = _format_inline(h_match.group(2))
            if level == 1:
                html_lines.append(
                    f"{closed}<h1 style='color: {_css_color('accent')}; font-size: 20px; "
                    f"font-weight: bold; margin: 12px 0 6px 0;'>{heading_text}</h1>"
                )
            elif level == 2:
                html_lines.append(
                    f"{closed}<h2 style='color: {_css_color('accent')}; font-size: 17px; "
                    f"font-weight: bold; margin: 10px 0 5px 0;'>{heading_text}</h2>"
                )
            elif level == 3:
                html_lines.append(
                    f"{closed}<h3 style='color: {_css_color('accent')}; font-size: 15px; "
                    f"font-weight: bold; margin: 8px 0 4px 0;'>{heading_text}</h3>"
                )
            else:
                html_lines.append(
                    f"{closed}<h4 style='color: {_css_color('primary')}; font-size: 13px; "
                    f"font-weight: bold; margin: 6px 0 3px 0;'>{heading_text}</h4>"
                )
            last_block_was_paragraph = False
            continue

        # Blockquote
        q_match = _QUOTE_PATTERN.match(line)
        if q_match:
            closed = close_lists()
            q_text = _format_inline(q_match.group(1))
            html_lines.append(
                f"{closed}<div style='border-left: 3px solid {_css_color('accent')}; "
                f"margin: 4px 0 4px 6px; padding: 2px 0 2px 10px; "
                f"color: {_css_color('secondary')}; font-style: italic;'>{q_text}</div>"
            )
            last_block_was_paragraph = False
            continue

        # Unordered list: -, *, +
        ul_match = _UL_PATTERN.match(line)
        if ul_match:
            item_text = _format_inline(ul_match.group(1))
            if not in_ul:
                closed = close_lists()
                html_lines.append(f"{closed}<ul style='margin: 1px 0; padding-left: 20px;'>")
                in_ul = True
            html_lines.append(
                f"<li style='margin: 1px 0; color: {_css_color('secondary')}; "
                f"line-height: 1.4;'>{item_text}</li>"
            )
            last_block_was_paragraph = False
            continue

        # Ordered list: 1., 2., etc.
        ol_match = _OL_PATTERN.match(line)
        if ol_match:
            item_text = _format_inline(ol_match.group(2))
            if not in_ol:
                closed = close_lists()
                html_lines.append(f"{closed}<ol style='margin: 1px 0; padding-left: 20px;'>")
                in_ol = True
            html_lines.append(
                f"<li style='margin: 1px 0; color: {_css_color('secondary')}; "
                f"line-height: 1.4;'>{item_text}</li>"
            )
            last_block_was_paragraph = False
            continue

        # Normal paragraph text
        closed = close_lists()
        p_text = _format_inline(line)
        html_lines.append(
            f"{closed}<p style='margin: 4px 0 10px 0; line-height: 1.5; "
            f"color: {_css_color('secondary')}; font-size: 15px;'>{p_text}</p>"
        )
        last_block_was_paragraph = True

    html_lines.append(close_lists())
    return "".join(html_lines)
