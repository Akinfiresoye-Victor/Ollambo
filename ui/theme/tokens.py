"""Immutable visual tokens shared by Qt styles and rich-text renderers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Colors:
    window: str
    sidebar: str
    surface: str
    raised: str
    border_subtle: str
    border: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    accent: str
    accent_hover: str
    text_on_accent: str
    success: str
    warning: str
    danger: str
    info: str
    code_background: str
    code_text: str


@dataclass(frozen=True)
class Fonts:
    ui: tuple[str, ...]
    mono: tuple[str, ...]


@dataclass(frozen=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True)
class Radii:
    control: int = 10
    card: int = 16


@dataclass(frozen=True)
class Sizes:
    control_height: int = 32
    sidebar_width: int = 264
    content_max_width: int = 760
    inspector_width: int = 420
    icon_small: int = 16
    icon_medium: int = 18
    icon_large: int = 20


@dataclass(frozen=True)
class TypeScale:
    caption: int = 12
    body: int = 14
    chat: int = 15
    title: int = 18
    hero: int = 24


@dataclass(frozen=True)
class Theme:
    colors: Colors
    fonts: Fonts
    spacing: Spacing
    radii: Radii
    sizes: Sizes
    type_scale: TypeScale


DARK = Theme(
    colors=Colors(
        window="#1C1B1A",
        sidebar="#171615",
        surface="#242322",
        raised="#2D2B2A",
        border_subtle="#302E2C",
        border="#3B3836",
        text_primary="#ECE9E4",
        text_secondary="#B4AFA8",
        text_tertiary="#8A857E",
        accent="#CC7E5B",
        accent_hover="#D98F6E",
        text_on_accent="#1C1B1A",
        success="#7FB069",
        warning="#E0B44C",
        danger="#E5737B",
        info="#6FA8DC",
        code_background="#161514",
        code_text="#D8D4CD",
    ),
    fonts=Fonts(
        ui=(
            "Segoe UI Variable Text",
            "Segoe UI",
            "SF Pro Text",
            "Inter",
            "Helvetica Neue",
            "Arial",
        ),
        mono=("Cascadia Code", "JetBrains Mono", "SF Mono", "Consolas", "monospace"),
    ),
    spacing=Spacing(),
    radii=Radii(),
    sizes=Sizes(),
    type_scale=TypeScale(),
)
