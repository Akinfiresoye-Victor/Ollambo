"""Qt stylesheet and palette construction from shared theme tokens."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from ui.theme.tokens import DARK, Theme


def _font_stack(families: tuple[str, ...]) -> str:
    return ", ".join(f'"{family}"' for family in families)


def build_stylesheet(theme: Theme = DARK) -> str:
    c = theme.colors
    s = theme.sizes
    t = theme.type_scale
    r = theme.radii
    ui_font = _font_stack(theme.fonts.ui)
    mono_font = _font_stack(theme.fonts.mono)
    return f"""
        QWidget {{
            color: {c.text_primary};
            font-family: {ui_font};
            font-size: {t.body}px;
        }}
        QMainWindow, QDialog, QMessageBox, QInputDialog {{
            background-color: {c.window};
        }}
        QTabWidget::pane {{
            border: 1px solid {c.border_subtle};
            border-radius: {r.control}px;
            background-color: {c.window};
        }}
        QTabBar::tab {{
            color: {c.text_secondary};
            background-color: {c.window};
            border: 1px solid transparent;
            border-radius: {r.control}px;
            min-height: {s.control_height}px;
            padding: 0 12px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            color: {c.text_primary};
            background-color: {c.surface};
            border-color: {c.border_subtle};
        }}
        QTabBar::tab:hover {{ background-color: {c.raised}; }}
        QWidget#Sidebar {{ background-color: {c.sidebar}; }}
        QWidget#Sidebar QLabel {{ background-color: transparent; }}
        QFrame#Card, QFrame[role="card"] {{
            background-color: {c.surface};
            border: 1px solid {c.border_subtle};
            border-radius: {r.card}px;
        }}
        QFrame[messageRole="user"] {{ background-color: transparent; border: none; }}
        QFrame[messageRole="assistant"] {{ background-color: transparent; border: none; border-radius: 0; }}
        QWidget#UserMessageBubble {{
            background-color: {c.raised};
            border: 1px solid {c.border_subtle};
            border-radius: {r.card}px;
        }}
        QWidget#AttachmentChip {{
            background-color: {c.raised};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
        }}
        QLabel[role="sender"] {{
            color: {c.text_primary};
            font-size: {t.caption}px;
            font-weight: 600;
        }}
        QLabel[role="tertiary"][active="true"] {{
            color: {c.text_primary};
            background-color: {c.raised};
            border-radius: {r.control}px;
            font-weight: 600;
        }}
        QLabel[role="attachment"] {{
            color: {c.text_secondary};
            background-color: {c.raised};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
            padding: 2px 8px;
        }}
        QLabel[role="title"] {{
            color: {c.text_primary};
            font-size: {t.title}px;
            font-weight: 600;
        }}
        QLabel[role="hero"] {{
            color: {c.text_primary};
            font-size: {t.hero}px;
            font-weight: 600;
        }}
        QLabel[role="muted"], QLabel[role="caption"] {{
            color: {c.text_secondary};
        }}
        QLabel[role="caption"] {{ font-size: {t.caption}px; }}
        QLabel[role="tertiary"] {{ color: {c.text_tertiary}; }}
        QLabel[role="accent"] {{ color: {c.accent}; }}
        QLabel[role="section"] {{
            color: {c.text_secondary};
            font-size: {t.caption}px;
            font-weight: 600;
        }}
        QLabel[role="body"] {{ color: {c.text_secondary}; font-size: {t.chat}px; }}
        QLabel[role="success"], QLabel[state="success"] {{ color: {c.success}; }}
        QLabel[role="warning"], QLabel[state="warning"] {{ color: {c.warning}; }}
        QLabel[role="danger"], QLabel[state="danger"] {{ color: {c.danger}; }}
        QLabel[role="info"], QLabel[state="info"] {{ color: {c.info}; }}
        QLabel#ConnectionBadge[state="active"],
        QLabel#ConnectionBadge[state="connected"] {{ color: {c.accent}; }}
        QLabel#ConnectionBadge[state="standby"] {{ color: {c.text_tertiary}; }}
        QLabel#ConnectionBadge[state="warning"] {{ color: {c.warning}; }}
        QLabel#TokenMeter[state="warning"] {{ color: {c.warning}; }}
        QLabel[role="code"], QPlainTextEdit[role="code"] {{
            color: {c.code_text};
            background-color: {c.code_background};
            font-family: {mono_font};
        }}
        QPushButton, QToolButton {{
            color: {c.text_primary};
            background-color: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
            height: {s.control_height}px;
            min-width: {s.control_height}px;
            padding: 0 12px;
        }}
        QPushButton:hover, QToolButton:hover {{ background-color: {c.raised}; border-color: {c.border}; }}
        QPushButton:pressed, QToolButton:pressed {{ background-color: {c.border}; }}
        QPushButton:focus, QToolButton:focus,
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
        QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {c.accent};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {c.text_tertiary};
            background-color: {c.window};
            border-color: {c.border_subtle};
        }}
        QPushButton[variant="primary"], QToolButton[variant="primary"] {{
            color: {c.text_on_accent};
            background-color: {c.accent};
            border-color: {c.accent};
            font-weight: 600;
        }}
        QPushButton[variant="primary"]:hover, QToolButton[variant="primary"]:hover {{
            background-color: {c.accent_hover};
            border-color: {c.accent_hover};
        }}
        QPushButton[variant="primary"]:disabled, QToolButton[variant="primary"]:disabled {{
            color: {c.text_on_accent};
            background-color: {c.accent_hover};
            border-color: {c.accent_hover};
            opacity: 0.7;
        }}
        QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
            background-color: transparent;
            border-color: transparent;
        }}
        QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
            background-color: {c.raised};
            border: 1px solid {c.border_subtle};
        }}
        QPushButton[variant="ghost"]:pressed, QToolButton[variant="ghost"]:pressed {{
            background-color: {c.raised};
            border: 1px solid {c.border};
        }}
        QPushButton[variant="ghost"]:focus, QToolButton[variant="ghost"]:focus {{
            border: 1px solid {c.accent};
        }}
        QComboBox[role="chip"] {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: {r.control}px;
            padding: 0 10px;
            min-height: {s.control_height}px;
        }}
        QComboBox[role="chip"]:hover {{
            background-color: {c.raised};
            border-color: {c.border_subtle};
        }}
        QPushButton[variant="danger"] {{
            color: {c.danger};
            background-color: transparent;
            border-color: {c.border};
        }}
        QPushButton[iconOnly="true"], QToolButton[iconOnly="true"] {{
            min-width: {s.control_height}px;
            max-width: {s.control_height}px;
            padding: 0;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox {{
            color: {c.text_primary};
            background-color: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
            padding: 6px 8px;
            selection-background-color: {c.accent};
            selection-color: {c.text_on_accent};
        }}
        QTextBrowser[role="body"] {{
            background: transparent;
            border: none;
            padding: 0;
        }}
        QWidget#Composer {{
            background-color: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.card}px;
        }}
        QTextEdit#ComposerInput, QPlainTextEdit#ComposerInput {{
            border: none;
            background: transparent;
            padding: 0;
        }}
        QToolButton#ComposerSendButton {{
            border-radius: {r.card}px;
            min-width: 32px;
            max-width: 32px;
        }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}
        QLineEdit, QComboBox, QSpinBox {{ height: {s.control_height}px; }}
        QPlainTextEdit[role="code"] {{
            border-color: {c.border_subtle};
            border-radius: {r.control}px;
            padding: 8px;
        }}
        QComboBox::drop-down {{ border: 0; width: 24px; }}
        QComboBox QAbstractItemView {{
            color: {c.text_primary};
            background-color: {c.surface};
            border: 1px solid {c.border};
            selection-background-color: {c.raised};
            selection-color: {c.text_primary};
        }}
        QListWidget, QTreeWidget, QTableWidget {{
            color: {c.text_primary};
            background-color: {c.surface};
            alternate-background-color: {c.window};
            border: 1px solid {c.border_subtle};
            border-radius: {r.control}px;
            gridline-color: {c.border_subtle};
            outline: none;
        }}
        QListWidget::item, QTreeWidget::item, QTableWidget::item {{
            padding: 6px;
        }}
        QListWidget::item:selected, QTreeWidget::item:selected,
        QTableWidget::item:selected {{
            color: {c.text_primary};
            background-color: {c.raised};
        }}
        QHeaderView::section {{
            color: {c.text_secondary};
            background-color: {c.surface};
            border: 0;
            border-bottom: 1px solid {c.border_subtle};
            padding: 8px;
            font-weight: 600;
        }}
        QScrollBar:vertical {{
            width: 8px;
            background: transparent;
            margin: 0;
        }}
        QScrollBar:horizontal {{
            height: 8px;
            background: transparent;
            margin: 0;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background-color: {c.border};
            border-radius: 4px;
            min-width: 24px;
            min-height: 24px;
        }}
        QScrollBar::handle:hover {{ background-color: {c.text_tertiary}; }}
        QScrollBar::add-line, QScrollBar::sub-line,
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: 0; }}
        QSplitter::handle {{ background-color: {c.border_subtle}; }}
        QSplitter::handle:hover {{ background-color: {c.border}; }}
        QMenu {{
            color: {c.text_primary};
            background-color: {c.surface};
            border: 1px solid {c.border};
            padding: 4px;
        }}
        QMenu::item {{ padding: 6px 24px; }}
        QMenu::item:selected {{ background-color: {c.raised}; }}
        QToolTip {{
            color: {c.text_primary};
            background-color: {c.raised};
            border: 1px solid {c.border};
            padding: 4px 6px;
        }}
        QWidget#Toast {{
            background-color: rgba(28, 27, 26, 220);
            border: 1px solid {c.border};
            border-radius: 12px;
        }}
        QLabel#ToastLabel {{
            color: {c.text_primary};
            background: transparent;
            padding: 8px 12px;
        }}
        QProgressBar {{
            height: 4px;
            min-height: 4px;
            max-height: 4px;
            color: transparent;
            background-color: {c.raised};
            border: 0;
            border-radius: 2px;
        }}
        QProgressBar#ChatStatusProgressBar {{
            height: 2px;
            min-height: 2px;
            max-height: 2px;
        }}
        QProgressBar::chunk {{ background-color: {c.accent}; border-radius: 2px; }}
        QCheckBox {{ spacing: 8px; }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {c.border};
            border-radius: 4px;
            background-color: {c.surface};
        }}
        QCheckBox::indicator:checked {{
            background-color: {c.accent};
            border-color: {c.accent};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: 0; }}
        QDialogButtonBox QPushButton {{ min-width: 80px; }}
    """


def apply_theme(app: QApplication, theme: Theme = DARK) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    c = theme.colors
    roles = {
        QPalette.ColorRole.Window: c.window,
        QPalette.ColorRole.WindowText: c.text_primary,
        QPalette.ColorRole.Base: c.surface,
        QPalette.ColorRole.AlternateBase: c.raised,
        QPalette.ColorRole.Text: c.text_primary,
        QPalette.ColorRole.Button: c.surface,
        QPalette.ColorRole.ButtonText: c.text_primary,
        QPalette.ColorRole.Highlight: c.accent,
        QPalette.ColorRole.HighlightedText: c.text_on_accent,
        QPalette.ColorRole.ToolTipBase: c.raised,
        QPalette.ColorRole.ToolTipText: c.text_primary,
        QPalette.ColorRole.PlaceholderText: c.text_tertiary,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.text_tertiary))
    app.setPalette(palette)

    font = QFont()
    font.setFamilies(list(theme.fonts.ui))
    font.setPointSize(theme.type_scale.body)
    app.setFont(font)
    app.setStyleSheet(build_stylesheet(theme))

    from core.markdown_formatter import set_palette

    def rgb(value: str) -> tuple[int, int, int]:
        color = QColor(value)
        return color.red(), color.green(), color.blue()

    set_palette(
        accent=rgb(c.accent),
        primary=rgb(c.text_primary),
        secondary=rgb(c.text_secondary),
        tertiary=rgb(c.text_tertiary),
        bold=rgb(c.text_primary),
        italic=rgb(c.text_secondary),
        heading_subtle=rgb(c.text_primary),
        code_background=rgb(c.code_background),
        code_text=rgb(c.code_text),
        border=rgb(c.border),
        raised=rgb(c.raised),
        mono_font=", ".join(theme.fonts.mono),
    )


def set_variant(widget: QWidget, variant: str) -> None:
    set_property(widget, "variant", variant)


def set_property(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
