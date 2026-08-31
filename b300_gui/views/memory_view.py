"""Memory Map and Metadata Inspection View."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from b300_core.models import ProbeRef
from b300_gui.memory_tab import MemoryTab
from b300_gui.widgets.memory_map_widget import MemoryMapWidget


class MemoryView(QWidget):
    """Memory & Metadata Workspace with Visual Flash Map."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(
        self,
        service,
        probe_provider: Callable[[], ProbeRef],
        log_sink: Callable[[str], None] = lambda _line: None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        # 1. Top Visual Memory Map Bar
        self.memory_map = MemoryMapWidget(self)
        layout.addWidget(self.memory_map)

        # 2. Memory Tab Engine (Sectors, Metadata, Hex Preview, Options)
        self.inner_tab = MemoryTab(service, probe_provider, log_sink=log_sink)
        self.inner_tab.operation_state_changed.connect(self.operation_state_changed.emit)
        layout.addWidget(self.inner_tab, 1)

    def reload(self) -> None:
        self.inner_tab.reload()
