"""Dedicated Zero-Halt Live Monitor view for B300 v0.18.

The production MainWindow passes the already-wired ``DebugTab.live_panel`` into
this view exactly once during construction.  That preserves the proven
LiveMonitorSession backend and signal wiring while giving the panel one stable
production owner for the lifetime of the window.  Page navigation never
reparents the widget and never starts sampling by itself.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from b300_gui.debug_live_panel import DebugLivePanel


class MonitorView(QWidget):
    """Zero-halt Monitor page with permanent live-panel ownership."""

    def __init__(self, parent: Optional[QWidget] = None,
                 *, live_panel: Optional[DebugLivePanel] = None) -> None:
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self.live_panel = live_panel or DebugLivePanel(self)
        # One-time ownership transfer at construction.  Never reparent on page
        # switches; all backend signal connections on a supplied panel survive.
        if self.live_panel.parent() is not self:
            self.live_panel.setParent(self)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        banner = QFrame()
        banner.setObjectName("headerRibbon")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        title_row = QHBoxLayout()
        title = QLabel("LIVE MONITOR · REALTIME ZERO-HALT")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        badge = QLabel("ZERO-HALT")
        badge.setObjectName("safeBadge")
        title_row.addWidget(badge)
        title_row.addStretch(1)
        banner_layout.addLayout(title_row)
        description = QLabel(
            "Theo dõi RAM/DWT qua SWD mà không chủ động halt/reset MCU. "
            "Interactive breakpoint/step nằm riêng trong DEBUG và chỉ chạy sau hành động rõ ràng của người dùng."
        )
        description.setWordWrap(True)
        description.setObjectName("pageSubtitle")
        banner_layout.addWidget(description)
        layout.addWidget(banner)
        layout.addWidget(self.live_panel, 1)

    @property
    def buffer(self):
        return self.live_panel.buffer

    @property
    def table(self):
        return self.live_panel.table

    def set_control_state(self, *args, **kwargs):
        return self.live_panel.set_control_state(*args, **kwargs)

    def append_live_sample(self, sample):
        return self.live_panel.append_live_sample(sample)

    def apply_analytics(self, snapshot):
        return self.live_panel.apply_analytics(snapshot)

    def reset_for_sampling(self):
        return self.live_panel.reset_for_sampling()

    def mark_stopping(self):
        return self.live_panel.mark_stopping()

    def mark_live_completed(self, summary):
        return self.live_panel.mark_live_completed(summary)

    def mark_failed(self, message):
        return self.live_panel.mark_failed(message)


__all__ = ["MonitorView"]
