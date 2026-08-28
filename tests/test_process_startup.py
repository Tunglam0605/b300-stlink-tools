from __future__ import annotations

import subprocess
import unittest

from b300_core.process_startup import child_process_kwargs


class ChildProcessStartupTests(unittest.TestCase):
    def test_windows_child_process_is_hidden_without_losing_pipes(self) -> None:
        kwargs = child_process_kwargs(platform_name="windows")
        self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_non_windows_child_process_has_no_windows_creation_flags(self) -> None:
        self.assertEqual(child_process_kwargs(platform_name="linux"), {})


if __name__ == "__main__":
    unittest.main()
