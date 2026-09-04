from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from b300_core.vscode_environment import inspect_vscode_environment


class VsCodeEnvironmentTests(unittest.TestCase):
    def test_windows_uses_code_cli_launcher_to_list_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code_exe = root / "Code.exe"
            code_exe.write_bytes(b"")
            code_cmd = root / "bin" / "code.cmd"
            code_cmd.parent.mkdir()
            code_cmd.write_text("@echo off\n", encoding="ascii")
            invocations = []

            def run_factory(args, **kwargs):
                invocations.append((args, kwargs))
                output = (
                    "marus25.cortex-debug\n"
                    if Path(args[0]).name.lower() == "code.cmd"
                    else ""
                )
                return SimpleNamespace(returncode=0, stdout=output)

            with patch(
                "b300_core.vscode_environment.resolve_vscode",
                return_value=str(code_exe),
            ), patch(
                "b300_core.vscode_environment.resolve_gdb",
                return_value="C:/B300/vendor/gdb/bin/arm-none-eabi-gdb.exe",
            ):
                status = inspect_vscode_environment(
                    run_factory=run_factory,
                    platform_name="windows",
                )

        self.assertTrue(status.cortex_debug_ready)
        self.assertTrue(status.ready)
        self.assertEqual(status.vscode_path, str(code_exe))
        self.assertEqual(Path(invocations[0][0][0]), code_cmd)
        self.assertIs(invocations[0][1]["shell"], False)

    def test_ready_when_vscode_cortex_debug_and_gdb_are_available(self) -> None:
        def run_factory(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="ms-python.python\nmarus25.cortex-debug\n",
            )

        with patch("b300_core.vscode_environment.resolve_vscode", return_value="/opt/code"):
            with patch("b300_core.vscode_environment.resolve_gdb", return_value="/opt/arm-none-eabi-gdb"):
                status = inspect_vscode_environment(run_factory=run_factory)
        self.assertTrue(status.ready)
        self.assertEqual(status.vscode_path, "/opt/code")
        self.assertEqual(status.gdb_path, "/opt/arm-none-eabi-gdb")

    def test_missing_cortex_debug_is_reported_without_touching_hardware(self) -> None:
        def run_factory(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout="ms-python.python\n")

        with patch("b300_core.vscode_environment.resolve_vscode", return_value="/opt/code"):
            with patch("b300_core.vscode_environment.resolve_gdb", return_value="/opt/gdb"):
                status = inspect_vscode_environment(run_factory=run_factory)
        self.assertTrue(status.vscode_ready)
        self.assertFalse(status.cortex_debug_ready)
        self.assertTrue(status.gdb_ready)
        self.assertIn("Cortex-Debug", status.reason)

    def test_missing_vscode_short_circuits_other_checks(self) -> None:
        with patch(
            "b300_core.vscode_environment.resolve_vscode",
            side_effect=FileNotFoundError("missing vscode"),
        ):
            status = inspect_vscode_environment()
        self.assertFalse(status.ready)
        self.assertFalse(status.vscode_ready)
        self.assertFalse(status.cortex_debug_ready)
        self.assertFalse(status.gdb_ready)
        self.assertIn("missing vscode", status.reason)

    def test_missing_gdb_is_reported(self) -> None:
        def run_factory(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout="marus25.cortex-debug\n")

        with patch("b300_core.vscode_environment.resolve_vscode", return_value="/opt/code"):
            with patch(
                "b300_core.vscode_environment.resolve_gdb",
                side_effect=FileNotFoundError("missing gdb"),
            ):
                status = inspect_vscode_environment(run_factory=run_factory)
        self.assertTrue(status.vscode_ready)
        self.assertTrue(status.cortex_debug_ready)
        self.assertFalse(status.gdb_ready)
        self.assertIn("arm-none-eabi-gdb", status.reason)


if __name__ == "__main__":
    unittest.main()
