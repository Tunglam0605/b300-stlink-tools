"""Gateway Setup & SSH Assistant View."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from b300_gui.gateway_setup_tab import GatewaySetupTab


class GatewayView(QWidget):
    """SSH Gateway setup and remote debug preparation view."""

    operation_state_changed = Signal()
    log = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, auto_refresh: bool = False) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        self.inner_tab = GatewaySetupTab(self, auto_refresh=auto_refresh)
        self.inner_tab.log.connect(self.log.emit)
        self.inner_tab.operation_state_changed.connect(self.operation_state_changed.emit)
        layout.addWidget(self.inner_tab, 1)

    def refresh(self) -> None:
        self.inner_tab.refresh_all()
