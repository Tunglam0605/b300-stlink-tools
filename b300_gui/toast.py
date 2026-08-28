"""Modern floating Toast notification widget with hover-to-pause for B300 ST-Link Tools."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPropertyAnimation, QEasingCurve, QTimer, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class ToastNotification(QFrame):
    """Floating notification toast that auto-dismisses after 2.5s and pauses on mouse hover."""

    def __init__(
        self,
        message: str,
        state: str = "normal",
        parent: QWidget | None = None,
        duration_ms: int = 2500,
    ) -> None:
        super().__init__(parent)
        self.duration_ms = duration_ms
        self._state = state
        self._paused = False

        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._build_ui(message, state)
        self._setup_timer()
        self._apply_shadow()

    def _build_ui(self, message: str, state: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        # Style themes for different notification states
        palettes = {
            "success": {
                "icon": "✓",
                "bg": "#ECFDF5",
                "border": "#A7F3D0",
                "fg": "#065F46",
                "icon_bg": "#10B981",
                "icon_fg": "#FFFFFF",
            },
            "error": {
                "icon": "✕",
                "bg": "#FEF2F2",
                "border": "#FECACA",
                "fg": "#991B1B",
                "icon_bg": "#EF4444",
                "icon_fg": "#FFFFFF",
            },
            "busy": {
                "icon": "●",
                "bg": "#FFFBEB",
                "border": "#FDE68A",
                "fg": "#92400E",
                "icon_bg": "#F59E0B",
                "icon_fg": "#FFFFFF",
            },
            "normal": {
                "icon": "ℹ",
                "bg": "#F0F9FF",
                "border": "#BAE6FD",
                "fg": "#0369A1",
                "icon_bg": "#0284C7",
                "icon_fg": "#FFFFFF",
            },
        }

        theme = palettes.get(state, palettes["normal"])

        self.setStyleSheet(
            "QFrame { background-color: %s; border: 1px solid %s; border-radius: 8px; }"
            % (theme["bg"], theme["border"])
        )

        icon_label = QLabel(theme["icon"])
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(
            "font-weight: 700; font-size: 11px; color: %s; background-color: %s; "
            "border-radius: 9px; min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; border: none;"
            % (theme["icon_fg"], theme["icon_bg"])
        )
        layout.addWidget(icon_label)

        self.text_label = QLabel(message)
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet(
            "color: %s; font-size: 12px; font-weight: 600; border: none; background: transparent;"
            % theme["fg"]
        )
        layout.addWidget(self.text_label, 1)

        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { color: #94A3B8; background: transparent; border: none; font-size: 11px; font-weight: 700; padding: 2px; } "
            "QPushButton:hover { color: %s; }" % theme["fg"]
        )
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn)

    def _apply_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(15, 23, 42, 45))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def _setup_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)

    def show_toast(self, parent_widget: QWidget) -> None:
        self.setParent(parent_widget)
        self.adjustSize()
        self._reposition(parent_widget)
        self.show()
        self.raise_()
        self.timer.start(self.duration_ms)

    def _reposition(self, parent_widget: QWidget) -> None:
        p_rect = parent_widget.rect()
        margin_right = 20
        margin_bottom = 24
        x = p_rect.width() - self.width() - margin_right
        y = p_rect.height() - self.height() - margin_bottom
        self.move(max(10, x), max(10, y))

    def enterEvent(self, event) -> None:
        """Pause timer when user hovers mouse cursor over toast to read."""
        self._paused = True
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """Resume 2s countdown when mouse leaves the toast."""
        self._paused = False
        self.timer.start(2000)
        super().leaveEvent(event)

    def dismiss(self) -> None:
        self.timer.stop()
        self.hide()
        self.deleteLater()
