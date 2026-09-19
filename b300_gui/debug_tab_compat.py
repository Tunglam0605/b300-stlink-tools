"""Compatibility wrapper for the historical DebugTab package import.

Existing internal callers may import b300_gui.debug_tab.DebugTab and expect
the setup/Live surface immediately. The production executable uses
ProductionMainWindow and does not select the retired version-layer DebugTab stack.
Keep this shim thin until the package-level compatibility contract is retired.
"""

from __future__ import annotations

from .debug_tab import DebugTab as _RefactoredDebugTab


class DebugTabCompat(_RefactoredDebugTab):
    """Keep the established initial setup surface for compatibility imports."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.main_stack.setCurrentWidget(self.scroll_area)
