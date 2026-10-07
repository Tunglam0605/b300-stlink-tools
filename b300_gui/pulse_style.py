"""Scoped visual tokens for the compact production Pulse view."""

from __future__ import annotations


def pulse_stylesheet(dark: bool = False) -> str:
    canvas, surface, ink, muted, line = (
        ("#111A2D", "#18253C", "#F1F6FB", "#AEBED4", "#2D4264") if dark
        else ("#F4F7FD", "#FFFFFF", "#17233F", "#596982", "#D9E2F0")
    )
    soft = "#202F4A" if dark else "#EAF0FF"
    status_ink = "#BBD2FF" if dark else "#3659BB"
    illustration = "background: #F7FAFF; border: 1px solid #D9E2F0; border-radius: 16px; padding: 14px;" if dark else "background: transparent; border: none; padding: 0;"
    return f"""
    QWidget#PulseView {{ background: {canvas}; color: {ink}; }}
    QLabel#PulseTitle {{ font-family: "Segoe UI"; font-size: 30px; font-weight: 500; }}
    QWidget#PulseTaskPage, QStackedWidget#PulseStack {{ background: transparent; border: none; }}
    QFrame#PulseToolbar, QFrame#PulseDock {{ background: {surface}; border: 1px solid {line}; border-radius: 12px; }}
    QFrame#PulseWorkspace {{ background: {surface}; border: 1px solid {line}; border-radius: 18px; }}
    QLabel#PulseIllustration {{ {illustration} }}
    QLabel#PulseCaption {{ color: {muted}; }}
    QLabel#PulseStatus {{ color: {status_ink}; background: {soft}; border-radius: 8px; padding: 6px 10px; font-weight: 600; }}
    QLabel#PulseStatus[error="true"] {{ color: #A53A45; background: #FBECEE; }}
    QPushButton#PulsePrimary {{ background: #3659BB; color: white; border: none; border-radius: 9px; min-height: 42px; padding: 0 18px; font-weight: 700; }}
    QPushButton#PulseQuiet {{ background: transparent; border: 1px solid transparent; border-radius: 8px; min-height: 34px; padding: 0 12px; }}
    QPushButton#PulseQuiet:hover {{ background: {soft}; border-color: {line}; }}
    QPushButton#PulseNav {{ background: transparent; border: 1px solid transparent; border-radius: 9px; min-height: 42px; padding: 0 14px; text-align: left; }}
    QPushButton#PulseNav:checked {{ background: {soft}; color: {status_ink}; border-color: #89A5E7; font-weight: 700; }}
    QComboBox {{ min-height: 34px; border: 1px solid {line}; border-radius: 8px; padding: 2px 9px; background: {surface}; }}
    """
