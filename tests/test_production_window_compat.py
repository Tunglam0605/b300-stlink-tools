"""Production-window naming and compatibility contracts."""

from __future__ import annotations

import unittest

from b300_gui.__main__ import MainWindow as EntryMainWindow
from b300_gui.main_window_v18 import MainWindowV18
from b300_gui.production_window import ProductionMainWindow


class ProductionWindowCompatibilityTests(unittest.TestCase):
    def test_executable_uses_canonical_production_window(self) -> None:
        self.assertIs(EntryMainWindow, ProductionMainWindow)
        self.assertEqual(ProductionMainWindow.__module__, "b300_gui.production_window")

    def test_historical_main_window_v18_import_remains_compatible(self) -> None:
        self.assertIs(MainWindowV18, ProductionMainWindow)


if __name__ == "__main__":
    unittest.main()
