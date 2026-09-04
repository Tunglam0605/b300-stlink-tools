"""Dedicated Zero-Halt Live Monitor View for B300 ST-Link Tools (v0.18).

Provides safe realtime variable observation, DWT timeline sampling, and plotting
without intentionally halting or resetting the target MCU.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from b300_gui.debug_live_panel import DebugLivePanel


class MonitorView(QWidget):
    """Zero-halt Live Monitor view with permanent, stable panel ownership."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # 1. Zero-Halt Safety Banner & Distinction Header
        banner = QFrame()
        banner.setObjectName("headerRibbon")
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_lbl = QLabel("LIVE MONITOR · THEO DÕI REALTIME ZERO-HALT")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #10B981; letter-spacing: 0.5px;")
        title_row.addWidget(title_lbl)

        zero_halt_badge = QLabel("ZERO-HALT ACTIVE")
        zero_halt_badge.setStyleSheet(
            "font-size: 10px; font-weight: 800; font-family: monospace; "
            "padding: 2px 6px; border-radius: 3px; background: rgba(16, 185, 129, 0.15); color: #34D399;"
        )
        title_row.addWidget(zero_halt_badge)
        title_row.addStretch(1)
        banner_layout.addLayout(title_row)

        desc_lbl = QLabel(
            "Safe for realtime observation. Does not intentionally halt MCU. "
            "(Khác với Interactive Debug: Debug có thể HALT/Step vi điều khiển, "
            "Live Monitor chỉ quan sát dữ liệu RAM/DWT an toàn khi robot đang chạy)."
        )
        desc_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
        desc_lbl.setWordWrap(True)
        banner_layout.addWidget(desc_lbl)
        layout.addWidget(banner)

        # 2. Permanent, Dedicated Live Panel (Zero reparenting)
        self.live_panel = DebugLivePanel(self)
        layout.addWidget(self.live_panel, 1)

    # Proxy methods to keep DebugLivePanel API easily accessible
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
