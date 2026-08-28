from __future__ import annotations

import unittest

from b300_core.models import ProbeInfo
from b300_core.probe_selection import ProbeSelectionError, select_probe


class ProbeSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.two_probes = (
            ProbeInfo("FIRST", "ST-Link A", "test", "usb:1"),
            ProbeInfo("SECOND", "ST-Link B", "test", "usb:2"),
        )

    def test_single_serialless_probe_uses_safe_openocd_auto_selection(self) -> None:
        info, ref = select_probe((ProbeInfo(None, "Clone", "test", "usb:1"),), None)
        self.assertIsNone(ref.serial)
        self.assertEqual(info.usb_identity, "usb:1")

    def test_explicit_serial_selects_exact_physical_probe(self) -> None:
        info, ref = select_probe(self.two_probes, "SECOND")
        self.assertEqual(info.name, "ST-Link B")
        self.assertEqual(ref.serial, "SECOND")

    def test_no_probe_has_stable_reason_code(self) -> None:
        with self.assertRaises(ProbeSelectionError) as raised:
            select_probe((), None)
        self.assertEqual(raised.exception.code, "NO_PROBE")

    def test_explicit_missing_serial_has_stable_reason_code(self) -> None:
        with self.assertRaises(ProbeSelectionError) as raised:
            select_probe(self.two_probes, "MISSING")
        self.assertEqual(raised.exception.code, "PROBE_NOT_FOUND")

    def test_multiple_probes_without_explicit_match_are_ambiguous(self) -> None:
        with self.assertRaisesRegex(ProbeSelectionError, "multiple") as raised:
            select_probe(self.two_probes, None)
        self.assertEqual(raised.exception.code, "MULTIPLE_PROBES")

    def test_multiple_serialless_probes_cannot_be_pinned(self) -> None:
        probes = (
            ProbeInfo(None, "Clone A", "test", "usb:1"),
            ProbeInfo(None, "Clone B", "test", "usb:2"),
        )
        with self.assertRaises(ProbeSelectionError) as raised:
            select_probe(probes, None)
        self.assertEqual(raised.exception.code, "UNPINNABLE_MULTIPLE_PROBES")


if __name__ == "__main__":
    unittest.main()
