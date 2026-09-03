"""Compatibility wrapper for the pre-v0.15 DebugTab startup surface.

The Antigravity refactor moved mode selection into a dedicated first page.  Existing
internal callers and regression suites import ``b300_gui.debug_tab.DebugTab`` and
expect the setup/Live surface immediately.  Production v0.15 uses DebugTabV15 and
explicitly switches back to the mode selector after construction.
"""

from __future__ import annotations

from .debug_tab import DebugTab as _RefactoredDebugTab


class DebugTabCompat(_RefactoredDebugTab):
    """Keep the established initial setup surface for compatibility imports."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.main_stack.setCurrentWidget(self.scroll_area)
