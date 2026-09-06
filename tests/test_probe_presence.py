from __future__ import annotations

import unittest

from b300_core.models import ProbeInfo
from b300_core.probe_presence import ProbePresenceTracker


def probe(serial, attachment):
    return ProbeInfo(serial, "ST-Link", "linux-sysfs", attachment)


class ProbePresenceTests(unittest.TestCase):
    def test_replug_of_same_serial_changes_attachment_generation(self) -> None:
        tracker = ProbePresenceTracker(probe("SAFE123", "usb:1-1"))
        first = tracker.observe((probe("SAFE123", "usb:1-1"),))
        removed = tracker.observe(())
        replugged = tracker.observe((probe("SAFE123", "usb:1-3"),))
        self.assertEqual((first.kind, first.generation), ("PRESENT", 1))
        self.assertEqual((removed.kind, removed.generation), ("REMOVED", 1))
        self.assertEqual((replugged.kind, replugged.generation), ("REPLACED", 2))

    def test_same_usb_path_with_different_serial_is_ambiguous(self) -> None:
        tracker = ProbePresenceTracker(probe("SAFE123", "usb:1-1"))
        tracker.observe((probe("SAFE123", "usb:1-1"),))
        event = tracker.observe((probe("OTHER", "usb:1-1"),))
        self.assertEqual(event.kind, "AMBIGUOUS")
        self.assertIsNone(event.probe)

    def test_duplicate_matching_serial_is_ambiguous(self) -> None:
        tracker = ProbePresenceTracker(probe("CLONE", "usb:1"))
        event = tracker.observe((probe("CLONE", "usb:1"), probe("CLONE", "usb:2")))
        self.assertEqual(event.kind, "AMBIGUOUS")

    def test_serialless_probe_cannot_move_to_a_new_attachment_automatically(self) -> None:
        tracker = ProbePresenceTracker(probe(None, "usb:1"))
        tracker.observe((probe(None, "usb:1"),))
        tracker.observe(())
        event = tracker.observe((probe(None, "usb:2"),))
        self.assertEqual(event.kind, "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
