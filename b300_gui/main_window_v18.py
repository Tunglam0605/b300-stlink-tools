"""Compatibility shim for the historical production-window import path.

The canonical production implementation lives in :mod:`b300_gui.production_window`.
Keep this alias so existing internal tooling that imports MainWindowV18 does not
break while new code uses ProductionMainWindow directly.
"""

from __future__ import annotations

from .production_window import ProductionMainWindow

MainWindowV18 = ProductionMainWindow

__all__ = ["MainWindowV18"]
