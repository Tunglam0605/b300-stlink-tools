from pathlib import Path
import unittest
import build_native_bundle


class PulseResourcesTests(unittest.TestCase):
    def test_native_bundle_and_both_frozen_specs_include_pulse_artwork(self):
        root = Path(__file__).resolve().parents[1]
        names = {path.name for path in build_native_bundle.gui_resources('linux-x64')}
        for name in ('pulse-logo.png', 'pulse-wordmark.png'):
            self.assertIn(name, names)
            self.assertTrue((root / 'branding' / name).is_file())
            for spec in ('b300_gui.spec', 'b300_gui_windows.spec'):
                self.assertIn('"' + name + '"', (root / spec).read_text(encoding='utf-8'))
