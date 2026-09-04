"""v0.15.2 Debug Studio lifecycle fixes.

Keep the zero-halt realtime monitor owned by the normal Debug Studio setup page.
Interactive Debug uses its dedicated workstation, but must never reparent the
realtime panel into the workstation because doing so corrupts Qt layout ownership
and makes the normal monitor disappear after Disconnect.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from .debug_tab_v15 import DebugTabV15


class DebugTabV152(DebugTabV15):
    """v0.15.2 production Debug Studio with stable panel ownership."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._install_realtime_boundary_notice()
        self._ensure_realtime_panel_home()

    def _install_realtime_boundary_notice(self) -> None:
        """Explain why realtime monitoring is not embedded in Interactive Debug."""
        notice = QLabel(
            "Theo dõi realtime (zero-halt) được giữ ở màn hình Studio chính để "
            "không trộn vòng đời với Debug tương tác có khả năng HALT MCU. "
            "Ngắt Debug tương tác để quay lại Theo dõi realtime."
        )
        notice.setObjectName("interactiveRealtimeBoundaryNotice")
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setProperty("kind", "info")
        self.workstation.live_layout.addStretch(1)
        self.workstation.live_layout.addWidget(notice)
        self.workstation.live_layout.addStretch(1)
        self._interactive_realtime_notice = notice

    def _ensure_realtime_panel_home(self) -> None:
        """Repair legacy/accidental reparenting and restore the setup layout order."""
        layout = self.scroll_content.layout()
        if layout is None:
            return
        if self.live_panel.parentWidget() is self.scroll_content and layout.indexOf(self.live_panel) >= 0:
            return

        self.live_panel.setParent(self.scroll_content)
        plot_index = layout.indexOf(self.plot_panel)
        insert_at = plot_index if plot_index >= 0 else max(0, layout.count() - 2)
        layout.insertWidget(insert_at, self.live_panel)
        self.live_panel.show()

    def show_workstation(self) -> None:
        """Open Interactive Debug without moving the zero-halt realtime panel."""
        self._ensure_realtime_panel_home()
        self.main_stack.setCurrentWidget(self.workstation)

    def show_setup(self) -> None:
        """Return to normal Debug Studio with realtime monitoring still present."""
        self._ensure_realtime_panel_home()
        super().show_setup()

    def _stopped(self, before) -> None:
        """Disconnect Interactive Debug and restore the normal Studio surface."""
        self._ensure_realtime_panel_home()
        super()._stopped(before)
        self._ensure_realtime_panel_home()
