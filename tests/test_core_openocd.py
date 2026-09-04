from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef, TargetInfo
from b300_core.openocd import (
    OpenOcdRunner,
    build_boot_verify_command,
    build_factory_flash_command,
    build_factory_protect_command,
    build_flash_command,
    build_metadata_write_command,
    build_reset_command,
    parse_boot_verification,
    parse_metadata_readback,
    build_target_inspect_command,
    parse_target_info,
    resolve_openocd,
)
from b300_core.policy import build_flash_plan
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


class OpenOcdCoreTests(unittest.TestCase):
    def test_frozen_runtime_ignores_tampered_adjacent_vendor_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "b300-stlink-gui.exe"
            bundled = root / "vendor" / "openocd" / "bin" / "openocd.exe"
            bundled.parent.mkdir(parents=True)
            executable.write_bytes(b"gui")
            bundled.write_bytes(b"tampered")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(executable)), \
                    mock.patch("b300_core.openocd.verify_openocd_tree", return_value=False), \
                    mock.patch("b300_core.openocd.installed_openocd_path") as installed, \
                    mock.patch("b300_core.openocd.shutil.which", return_value=None):
                installed.return_value = root / "missing-runtime" / "openocd.exe"
                self.assertEqual(resolve_openocd(), "openocd")

    def test_missing_environment_override_falls_back_to_verified_offline_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / ("openocd.exe" if os.name == "nt" else "openocd")
            installed.write_bytes(b"runtime")
            if os.name != "nt":
                installed.chmod(0o755)
            with mock.patch.dict(os.environ, {"B300_OPENOCD": "missing-openocd"}), \
                    mock.patch(
                        "b300_core.openocd.installed_openocd_path",
                        return_value=installed,
                    ), mock.patch(
                        "b300_core.openocd.verify_openocd_tree", return_value=True
                    ), mock.patch("b300_core.openocd.shutil.which", return_value=None):
                self.assertEqual(resolve_openocd(), str(installed))

    def test_application_root_resolves_verified_packaged_openocd_without_frozen_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "openocd.exe" if os.name == "nt" else "openocd"
            bundled = root / "vendor" / "openocd" / "bin" / name
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"runtime")
            if os.name != "nt":
                bundled.chmod(0o755)
            with mock.patch.dict(os.environ, {"B300_APP_ROOT": str(root)}, clear=False), \
                    mock.patch(
                        "b300_core.openocd.verify_openocd_tree", return_value=True
                    ), mock.patch(
                        "b300_core.openocd.installed_openocd_path",
                        return_value=root / "missing" / name,
                    ), mock.patch("b300_core.openocd.shutil.which", return_value=None):
                self.assertEqual(resolve_openocd(), str(bundled))

    def test_application_root_rejects_unverified_packaged_openocd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "openocd.exe" if os.name == "nt" else "openocd"
            bundled = root / "vendor" / "openocd" / "bin" / name
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"tampered")
            if os.name != "nt":
                bundled.chmod(0o755)
            with mock.patch.dict(os.environ, {"B300_APP_ROOT": str(root)}, clear=False), \
                    mock.patch(
                        "b300_core.openocd.verify_openocd_tree", return_value=False
                    ), mock.patch(
                        "b300_core.openocd.installed_openocd_path",
                        return_value=root / "missing" / name,
                    ), mock.patch("b300_core.openocd.shutil.which", return_value=None):
                self.assertEqual(resolve_openocd(), "openocd")

    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
        return build_flash_plan(
            image,
            ProbeRef(serial="SAFE123"),
            TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True),
        )

    def test_program_transaction_cannot_write_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            command = build_flash_command(plan, "openocd")

        rendered = "\n".join(command)
        self.assertIn("flash erase_sector 0 3 7", rendered)
        self.assertIn("flash write_image {%s}" % plan.image.path, rendered)
        self.assertIn("verify_image {%s}" % plan.image.path, rendered)
        self.assertNotIn("flash write_image erase", rendered)
        self.assertIn('echo "** Verified OK **"', rendered)
        self.assertNotIn("mww 0x40002860", rendered)
        self.assertIn("adapter serial SAFE123", command)
        self.assertIn("gdb port disabled", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)
        self.assertNotIn("mass_erase", rendered)

    def test_metadata_transaction_is_hard_bounded_to_sector_three_record(self) -> None:
        # A staging path may contain an OpenOCD command name by coincidence.
        with tempfile.TemporaryDirectory(prefix="mdw-path-") as directory:
            metadata = Path(directory) / "appmeta.bin"
            metadata.write_bytes(bytes(range(44)))
            readback = Path(directory) / "appmeta-readback.bin"
            command = build_metadata_write_command(
                ProbeRef("SAFE123"), metadata, readback, "openocd"
            )
        rendered = "\n".join(command)
        self.assertIn("reset halt", rendered)
        self.assertIn("flash write_image {%s} 0x0800C000 bin" % metadata.resolve(), rendered)
        self.assertIn("verify_image {%s} 0x0800C000 bin" % metadata.resolve(), rendered)
        self.assertIn("dump_image {%s} 0x0800C000 44" % readback.resolve(), rendered)
        openocd_commands = [
            command[index + 1]
            for index, argument in enumerate(command[:-1])
            if argument == "-c"
        ]
        for forbidden in ("mww", "mdw"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(any(
                    re.search(r"(?:^|[;{}]\s*)%s(?:\s|$)" % forbidden, script)
                    for script in openocd_commands
                ))
        self.assertNotIn("erase_sector", rendered)
        for forbidden in ("mass_erase", "flash protect", "erase_sector 0 0", "erase_sector 0 1",
                          "erase_sector 0 2", "stm32f2x lock", "stm32f2x unlock"):
            self.assertNotIn(forbidden, rendered)

    def test_metadata_readback_parser_reconstructs_exact_little_endian_bytes(self) -> None:
        output = (
            "0x0800c000: 53544c4d 00000001 00000002 00000008\n"
            "0x0800c010: 12345678 30303342 3034465f 00455a37\n"
            "0x0800c020: 00000001 a1b2c3d4 00000000\n"
        )
        data = parse_metadata_readback(output)
        self.assertEqual(len(data), 44)
        self.assertEqual(int.from_bytes(data[:4], "little"), 0x53544C4D)
        self.assertEqual(int.from_bytes(data[40:44], "little"), 0x00000000)

    def test_reset_is_a_separate_non_flash_transaction(self) -> None:
        reset = build_reset_command(ProbeRef("SAFE123"), "openocd")
        rendered_reset = "\n".join(reset)
        self.assertIn("reset run", reset)
        self.assertNotIn("mww", rendered_reset)
        self.assertNotIn("erase_sector", rendered_reset)
        self.assertNotIn("program {", rendered_reset)
        self.assertNotIn("flash protect", rendered_reset)

    def test_boot_verify_command_resumes_before_shutdown(self) -> None:
        command = build_boot_verify_command(ProbeRef(), "openocd")
        self.assertLess(command.index("resume"), command.index("shutdown"))
        self.assertIn("sleep 1000", command)
        self.assertNotIn("reset run", command)
        self.assertIn("mdw 0x40002854 1", command)
        self.assertNotIn("0x40002860", " ".join(command))

    def test_boot_verification_requires_application_pc_and_clear_recovery_slot(self) -> None:
        output = (
            "pc (/32): 0x0802496e\n"
            "0x40002854: 00000000\n"
        )
        result = parse_boot_verification(output)
        self.assertTrue(result.passed)
        self.assertEqual(result.pc, 0x0802496E)
        self.assertEqual(result.bkp1r, 0)

    def test_boot_verification_rejects_bootloader_pc(self) -> None:
        output = (
            "pc (/32): 0x08002138\n"
            "0x40002854: 00000000\n"
        )
        result = parse_boot_verification(output)
        self.assertFalse(result.passed)
        self.assertIn("Bootloader", result.reason)

    def test_runner_streams_output_and_returns_combined_result(self) -> None:
        events = []
        result = OpenOcdRunner().run(
            [sys.executable, "-c", "print('Verified OK')"],
            event_sink=events.append,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Verified OK", result.output)
        self.assertEqual(events, ["Verified OK"])

    def test_runner_reports_missing_executable_without_shell(self) -> None:
        result = OpenOcdRunner().run(["definitely-missing-b300-openocd"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.output.lower())

    def test_runner_passes_hidden_process_policy_without_losing_log_pipes(self) -> None:
        captured = {}

        class Process:
            stdout = io.StringIO("")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with mock.patch("b300_core.process_startup.subprocess.CREATE_NO_WINDOW", 0x08000000,
                        create=True):
            result = OpenOcdRunner(
                process_factory=lambda command, **kwargs: captured.update(kwargs) or Process(),
                platform_name="windows",
            ).run(["openocd"])

        self.assertEqual(result.returncode, 0)
        self.assertTrue(captured["creationflags"] & 0x08000000)
        self.assertEqual(captured["stdout"], subprocess.PIPE)
        self.assertEqual(captured["stderr"], subprocess.STDOUT)
        self.assertTrue(captured["text"])
        self.assertFalse(captured["shell"])

    def test_runner_timeout_terminates_process_and_preserves_output(self) -> None:
        started = time.monotonic()
        result = OpenOcdRunner().run(
            [sys.executable, "-c",
             "import time; print('started', flush=True); time.sleep(10)"],
            timeout_seconds=0.15,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertFalse(result.cancelled)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("started", result.output)
        self.assertLess(elapsed, 3.0)

    def test_runner_cancel_terminates_process_without_waiting_for_timeout(self) -> None:
        cancel = threading.Event()
        cancel.set()
        started = time.monotonic()
        result = OpenOcdRunner().run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=30,
            cancel_event=cancel,
        )
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_frozen_app_resolves_bundled_openocd(self) -> None:
        from b300_core.openocd import resolve_openocd

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / ("b300-stlink-gui.exe" if os.name == "nt" else
                                 "b300-stlink-gui")
            openocd_name = "openocd.exe" if os.name == "nt" else "openocd"
            bundled = root / "vendor" / "openocd" / "bin" / openocd_name
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")
            if os.name != "nt":
                bundled.chmod(0o755)
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(executable)), \
                 mock.patch(
                     "b300_core.openocd.verify_openocd_tree", return_value=True
                 ):
                self.assertTrue(os.path.samefile(resolve_openocd(), bundled))

    def test_target_inspection_is_read_only_and_parses_f407(self) -> None:
        command = build_target_inspect_command(ProbeRef("SAFE123"), "openocd")
        rendered = " ".join(command)
        self.assertIn("flash info 0", command)
        self.assertNotIn("halt", rendered)
        self.assertNotIn("reset", rendered)
        self.assertNotIn("mww", rendered)
        info = parse_target_info(
            "Info : Target voltage: 3.091421\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB\n"
            "#  0: 0x00000000 (0x4000 16kB) protected\n"
            "#  1: 0x00004000 (0x4000 16kB) protected\n"
            "#  2: 0x00008000 (0x4000 16kB) protected\n"
            "#  3: 0x0000c000 (0x4000 16kB) not protected\n"
            "#  4: 0x00010000 (0x10000 64kB) not protected\n"
            "#  5: 0x00020000 (0x20000 128kB) not protected\n"
            "#  6: 0x00040000 (0x20000 128kB) not protected\n"
            "#  7: 0x00060000 (0x20000 128kB) not protected\n"
        )
        self.assertEqual(info.device_id, 0x101F6413)
        self.assertEqual(info.flash_kib, 512)
        self.assertAlmostEqual(info.target_voltage, 3.091421)
        self.assertEqual(
            info.protection_summary,
            "Sector 0–2 protected; Sector 3–7 not protected",
        )


    def test_target_parser_reports_readout_protection_without_changing_it(self) -> None:
        info = parse_target_info(
            "Info : Target voltage: 3.10\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB\n"
            "Info : Device Security Bit Set\n"
            "#  0: 0x00000000 (0x4000 16kB) protected\n"
            "#  1: 0x00004000 (0x4000 16kB) protected\n"
            "#  2: 0x00008000 (0x4000 16kB) protected"
        )
        self.assertTrue(info.readout_protected)
        self.assertEqual(info.protected_sectors, (0, 1, 2))

    def test_target_parser_marks_truncated_sector_report_incomplete(self) -> None:
        info = parse_target_info(
            "Info : Target voltage: 3.10\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB\n"
            "#  0: 0x00000000 (0x4000 16kB) protected\n"
            "#  1: 0x00004000 (0x4000 16kB) protected\n"
            "#  2: 0x00008000 (0x4000 16kB) protected\n"
            "#  3: 0x0000c000 (0x4000 16kB) not protected"
        )

        self.assertFalse(info.protection_reported)
        self.assertEqual(info.protected_sectors, (0, 1, 2))



if __name__ == "__main__":
    unittest.main()
