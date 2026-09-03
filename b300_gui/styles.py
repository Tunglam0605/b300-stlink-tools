"""Clean Modern Industrial styling and QSS tokens for B300 ST-Link GUI."""

from __future__ import annotations

from b300_gui.theme import ThemeManager, generate_stylesheet

# Backward-compatibility token exports
COLOR_CANVAS = "#0D1117"
COLOR_SURFACE = "#161B22"
COLOR_SURFACE_MUTED = "#21262D"
COLOR_INPUT_BG = "#0D1117"
COLOR_TERMINAL_BG = "#05080C"

COLOR_BORDER = "#30363D"
COLOR_BORDER_STRONG = "#484F58"
COLOR_BORDER_MUTED = "#21262D"

COLOR_TEXT = "#F0F6FC"
COLOR_TEXT_SECONDARY = "#8B949E"
COLOR_TEXT_MUTED = "#6E7681"
COLOR_TEXT_EYEBROW = "#58A6FF"

COLOR_PRIMARY = "#1F6FEB"
COLOR_PRIMARY_HOVER = "#388BFD"
COLOR_ACCENT_GREEN = "#238636"
COLOR_ACCENT_GREEN_HOVER = "#2EA043"
COLOR_WARNING = "#D29922"
COLOR_DANGER = "#DA3633"


def get_current_stylesheet() -> str:
    return generate_stylesheet(ThemeManager.instance().palette)


# Dynamic property or fallback
APP_STYLE = generate_stylesheet(ThemeManager.instance().palette)
