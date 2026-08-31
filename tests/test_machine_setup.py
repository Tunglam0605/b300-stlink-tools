import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from b300_core.machine_setup import (
    SetupComponent, MachineSetupReport, inspect_windows_stlink_driver,
    find_local_stlink_driver_package, install_windows_stlink_driver, validate_stlink_driver_package,
)


def completed(argv, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(tuple(argv), code, stdout, stderr)


class MachineSetupTests(unittest.TestCase):
    def test_windows_driver_ready_when_connected_stlink_status_is_ok(self) -> None:
        def runner(argv, _timeout):
            self.assertIn("powershell", str(argv[0]).lower())
            return completed(argv, stdout='{"Status":"OK","FriendlyName":"ST-Link Debug","InstanceId":"USB\\\\VID_0483&PID_3748\\\\SAFE"}')
        report = inspect_windows_stlink_driver(runner=runner)
        self.assertEqual(report.state, "ready")
        self.assertFalse(report.installable)

    def test_windows_driver_missing_when_no_device_or_driver_store_entry(self) -> None:
        calls = []
        def runner(argv, _timeout):
            calls.append(tuple(argv))
            if "powershell" in str(argv[0]).lower():
                return completed(argv, stdout="")
            return completed(argv, stdout="Published Name: oem1.inf\nProvider Name: Microsoft")
        report = inspect_windows_stlink_driver(runner=runner)
        self.assertEqual(report.state, "missing")
        self.assertTrue(report.required)
        self.assertTrue(report.installable)
        self.assertGreaterEqual(len(calls), 2)

    def test_official_driver_package_validation_accepts_only_stlink_vid_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "stlink_dbg_winusb.inf"
            good.write_text("USB\\VID_0483&PID_3748\nSTMicroelectronics", encoding="utf-8")
            files = validate_stlink_driver_package(root)
            self.assertEqual(files, (good.resolve(),))

    def test_driver_package_validation_rejects_unrelated_inf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "other.inf").write_text("USB\\VID_1234&PID_5678", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "STSW-LINK009"):
                validate_stlink_driver_package(root)

    def test_local_driver_package_override_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stlink_dbg_winusb.inf").write_text(
                "USB\\VID_0483&PID_3748", encoding="utf-8"
            )
            with mock.patch.dict("os.environ", {"B300_STLINK_DRIVER_PACKAGE": str(root)}):
                self.assertEqual(find_local_stlink_driver_package(), root.resolve())

    def test_driver_install_is_idempotent_when_already_ready(self) -> None:
        calls = []
        def runner(argv, _timeout):
            calls.append(tuple(argv))
            return completed(argv, stdout='{"Status":"OK","FriendlyName":"ST-Link Debug","InstanceId":"USB\\\\VID_0483&PID_3748\\\\SAFE"}')
        result = install_windows_stlink_driver(runner=runner)
        self.assertTrue(result.succeeded)
        self.assertFalse(result.changed)
        self.assertEqual(len(calls), 1)

    def test_machine_report_required_ready_ignores_optional_component(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("driver", "Driver", "ready", True, False, "ok"),
            SetupComponent("ssh", "SSH", "optional", False, True, "optional"),
        ))
        self.assertTrue(report.required_ready)
        self.assertEqual(report.missing_required, ())


if __name__ == "__main__":
    unittest.main()
