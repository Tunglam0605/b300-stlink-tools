"""R&D Live Debug Studio View."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from b300_core.debug_service import DebugService
from b300_core.models import ProbeRef
from b300_gui.debug_tab import DebugTab


class DebugStudioView(QWidget):
    """R&D Live Debug Studio integrating Live Watch, Realtime Plotting, and GDB/TCL Inspector."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(
        self,
        service: DebugService,
        selected_probe: Callable[[], ProbeRef],
        parent: Optional[QWidget] = None,
        settings=None,
        probe_count: Optional[Callable[[], int]] = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        self.inner_tab = DebugTab(
            service,
            selected_probe,
            parent=self,
            settings=settings,
            probe_count=probe_count,
        )
        self.inner_tab.operation_state_changed.connect(self.operation_state_changed.emit)
        self.inner_tab.log.connect(self.log.emit)
        layout.addWidget(self.inner_tab, 1)

    def shutdown(self) -> None:
        self.inner_tab.shutdown()
