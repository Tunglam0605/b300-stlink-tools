"""Adapt reference card styling to the active desktop palette."""
import re
from PySide6.QtWidgets import QFrame, QWidget
from .theme import ThemePalette


def apply_reference_palette(root: QWidget, palette: ThemePalette) -> None:
    """Scope raw frame declarations and retain original colors across toggles.

    Existing state handlers may replace a widget stylesheet; detect that before
    mapping it so a warning remains a warning after switching themes.
    """
    colors = {
        "#F8FAFC": palette.text, "#CBD5E1": palette.text_secondary,
        "#94A3B8": palette.text_secondary, "#64748B": palette.text_muted,
        "#131D31": palette.surface, "#1A2844": palette.surface_raised,
        "#0E1626": palette.input_bg, "#0B111E": palette.canvas,
        "#223452": palette.border, "#2D446B": palette.border_strong,
        "#1E2D4A": palette.border, "#1E293B": palette.surface_sunken,
        "#334155": palette.surface_sunken, "#475569": palette.border_strong,
        "#38BDF8": palette.accent_cyan, "#10B981": palette.success,
        "#064E3B": palette.success_light, "#34D399": palette.success_hover,
        "#450A0A": palette.danger_light, "#F87171": palette.danger,
        "#FCA5A5": palette.danger, "#7F1D1D": palette.danger_light,
        "#0C4A6E": palette.primary_light, "#7DD3FC": palette.accent_cyan,
        "#2D1A04": palette.warning_light, "#FDE68A": palette.warning,
    }
    for widget in [root, *root.findChildren(QWidget)]:
        current = widget.styleSheet()
        if not current:
            continue
        previous = getattr(widget, "_reference_rendered_style", None)
        source = getattr(widget, "_reference_source_style", current) if current == previous else current
        widget._reference_source_style = source
        rendered = re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: colors.get(m[0].upper(), m[0]), source)
        if isinstance(widget, QFrame) and "{" not in rendered:
            # An unqualified frame stylesheet also paints its child labels.
            widget.setProperty("referenceStyleId", str(id(widget)))
            rendered = 'QFrame[referenceStyleId="%s"] { %s }' % (id(widget), rendered)
        widget._reference_rendered_style = rendered
        if current != rendered:
            widget.setStyleSheet(rendered)
