from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from b300_core.hardware_owner import FileHardwareOwner


class FileHardwareOwnerTests(unittest.TestCase):
    def test_second_process_cannot_claim_stlink_until_first_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.lock"
            owner = FileHardwareOwner(path)
            token = owner.acquire()
            code = (
                "from pathlib import Path; "
                "from b300_core.hardware_owner import FileHardwareOwner; "
                "FileHardwareOwner(Path(__import__('sys').argv[1])).acquire()"
            )
            try:
                blocked = subprocess.run(
                    [sys.executable, "-c", code, str(path)],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn("ST-Link is owned", blocked.stderr)
            finally:
                token.release()
            available = subprocess.run(
                [sys.executable, "-c", code, str(path)],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(available.returncode, 0, available.stderr)

    def test_same_process_can_nest_claim_for_canonical_flash_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = FileHardwareOwner(Path(directory) / "hardware.lock")
            outer = owner.acquire()
            inner = owner.acquire()
            inner.release()
            outer.release()
            owner.acquire().release()


if __name__ == "__main__":
    unittest.main()
