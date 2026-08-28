from __future__ import annotations

import unittest
from unittest import mock

from b300_core import process_startup
from b300_core.process_startup import child_process_kwargs


class ChildProcessStartupTests(unittest.TestCase):
    def test_windows_child_process_is_hidden_without_losing_pipes(self) -> None:
        windows_flag = 0x08000000
        with mock.patch.object(
                process_startup.subprocess, "CREATE_NO_WINDOW", windows_flag, create=True):
            kwargs = child_process_kwargs(platform_name="windows")
        self.assertTrue(kwargs["creationflags"] & windows_flag)

    def test_non_windows_child_process_has_no_windows_creation_flags(self) -> None:
        self.assertEqual(child_process_kwargs(platform_name="linux"), {})

    def test_windows_policy_remains_callable_when_host_lacks_windows_constant(self) -> None:
        process_module = process_startup.subprocess
        original = getattr(process_module, "CREATE_NO_WINDOW", None)
        had_constant = hasattr(process_module, "CREATE_NO_WINDOW")
        if had_constant:
            delattr(process_module, "CREATE_NO_WINDOW")
        try:
            self.assertEqual(child_process_kwargs(platform_name="windows")["creationflags"], 0)
        finally:
            if had_constant:
                setattr(process_module, "CREATE_NO_WINDOW", original)


if __name__ == "__main__":
    unittest.main()
