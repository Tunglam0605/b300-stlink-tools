from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.remote_program_history import RemoteProgramHistory


class RemoteProgramHistoryTests(unittest.TestCase):
    def test_job_id_is_available_after_client_restart_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recent.json"
            first = RemoteProgramHistory(path)
            first.save("gateway-1", "a" * 32)
            second = RemoteProgramHistory(path)
            self.assertEqual(second.get("gateway-1"), "a" * 32)
            self.assertNotIn("token", path.read_text(encoding="utf-8"))

    def test_rejects_invalid_job_id(self):
        with tempfile.TemporaryDirectory() as directory:
            history = RemoteProgramHistory(Path(directory) / "recent.json")
            with self.assertRaises(ValueError):
                history.save("gateway-1", "../other")


if __name__ == "__main__":
    unittest.main()
