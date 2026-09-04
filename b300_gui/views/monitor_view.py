"""Dedicated Zero-Halt Live Monitor view for B300 v0.18.

The production window already owns a fully wired ``DebugTab.live_panel`` backed
by ``LiveMonitorSession``.  This view adopts that panel exactly once during
construction, preserving the proven zero-halt backend while giving it a stable
production owner.  Page navigation never reparents it and never starts sampling.
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
        if live_panel is None and parent is not None:
            debug_tab = getattr(parent, "debug_tab", None)
            candidate = getattr(debug_tab, "live_panel", None)
            if isinstance(candidate, DebugLivePanel):
                live_panel = candidate
        self.live_panel = live_panel or DebugLivePanel(self)
        # One-time ownership transfer at construction. Never reparent on page
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
