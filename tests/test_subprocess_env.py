from __future__ import annotations

import unittest
from unittest import mock

from b300_core.subprocess_env import external_command_env


class ExternalCommandEnvironmentTests(unittest.TestCase):
    def test_frozen_linux_restores_original_library_path(self):
        source = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/tmp/_MEI/lib",
                  "LD_LIBRARY_PATH_ORIG": "/usr/local/lib:/opt/lib"}
        with mock.patch("b300_core.subprocess_env.os.name", "posix"):
            result = external_command_env(source, frozen=True)
        self.assertEqual(result["LD_LIBRARY_PATH"], "/usr/local/lib:/opt/lib")
        self.assertEqual(result["LD_LIBRARY_PATH_ORIG"], "/usr/local/lib:/opt/lib")

    def test_frozen_linux_removes_bundle_path_without_original(self):
        source = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/tmp/_MEI/lib"}
        with mock.patch("b300_core.subprocess_env.os.name", "posix"):
            result = external_command_env(source, frozen=True)
        self.assertNotIn("LD_LIBRARY_PATH", result)

    def test_non_frozen_environment_is_unchanged(self):
        source = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/custom/lib"}
        result = external_command_env(source, frozen=False)
        self.assertEqual(result, source)


if __name__ == "__main__":
    unittest.main()
