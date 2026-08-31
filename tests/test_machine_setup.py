import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from b300_core.machine_setup import (
    BUNDLED_STLINK_DRIVER_NAME, DriverPackageRequired, SetupComponent, MachineSetupReport,
    inspect_windows_stlink_driver, find_local_stlink_driver_package, install_windows_stlink_driver,
    validate_bundled_stlink_driver_archive, validate_stlink_driver_package,
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

    def test_windows_driver_store_marks_driver_ready_even_if_present_device_is_not_ok(self) -> None:
        def runner(argv, _timeout):
            if "powershell" in str(argv[0]).lower():
                return completed(
                    argv,
                    stdout='{"Status":"Error","FriendlyName":"STM32 STLink","InstanceId":"USB\\\\VID_0483&PID_3748\\\\SAFE"}',
                )
            return completed(
                argv,
                stdout=(
                    "Published Name: oem33.inf\n"
                    "Original Name: stlink_dbg_winusb.inf\n"
                    "Provider Name: STMicroelectronics"
                ),
            )
        report = inspect_windows_stlink_driver(runner=runner)
        self.assertEqual(report.state, "ready")
        self.assertFalse(report.installable)
        self.assertIn("rút/cắm lại", report.detail)

    def test_vcp_driver_alone_does_not_satisfy_debug_driver_requirement(self) -> None:
        def runner(argv, _timeout):
            if "powershell" in str(argv[0]).lower():
                return completed(argv, stdout="")
            return completed(
                argv,
                stdout=(
                    "Published Name: oem34.inf\n"
                    "Original Name: stlink_vcp.inf\n"
                    "Provider Name: STMicroelectronics"
                ),
            )
        report = inspect_windows_stlink_driver(runner=runner)
        self.assertEqual(report.state, "missing")
        self.assertTrue(report.installable)

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

    def test_bundled_driver_archive_fails_closed_on_wrong_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / BUNDLED_STLINK_DRIVER_NAME
            archive.write_bytes(b"not-the-trusted-driver")
            with self.assertRaisesRegex(ValueError, "checksum"):
                validate_bundled_stlink_driver_archive(archive)

    def test_driver_install_is_idempotent_when_already_ready(self) -> None:
        calls = []
        def runner(argv, _timeout):
            calls.append(tuple(argv))
            return completed(argv, stdout='{"Status":"OK","FriendlyName":"ST-Link Debug","InstanceId":"USB\\\\VID_0483&PID_3748\\\\SAFE"}')
        result = install_windows_stlink_driver(runner=runner)
        self.assertTrue(result.succeeded)
        self.assertFalse(result.changed)
        self.assertEqual(len(calls), 1)

    def test_missing_driver_package_does_not_raise_uac_for_noop_scan(self) -> None:
        calls = []
        def runner(argv, _timeout):
            calls.append(tuple(argv))
            if "powershell" in str(argv[0]).lower():
                return completed(argv, stdout="")
            return completed(argv, stdout="Published Name: oem1.inf\nProvider Name: Microsoft")
        with mock.patch("b300_core.machine_setup.find_local_stlink_driver_package", return_value=None):
            with self.assertRaises(DriverPackageRequired):
                install_windows_stlink_driver(runner=runner)
        joined = "\n".join(" ".join(call) for call in calls).lower()
        self.assertNotIn("start-process", joined)
        self.assertNotIn("/scan-devices", joined)

    def test_machine_report_required_ready_ignores_optional_component(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("driver", "Driver", "ready", True, False, "ok"),
            SetupComponent("ssh", "SSH", "optional", False, True, "optional"),
        ))
        self.assertTrue(report.required_ready)
        self.assertEqual(report.missing_required, ())


if __name__ == "__main__":
    unittest.main()
