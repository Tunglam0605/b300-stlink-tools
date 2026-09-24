from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import b300_stlink
from b300_core.hardware_owner import FileHardwareOwner
from b300_core.gateway_system_mode import IsolatedGatewayConfig


class FileHardwareOwnerTests(unittest.TestCase):
    def test_isolated_owner_ignores_user_home_and_runtime_override(self):
        from b300_core import hardware_owner, gateway_supervisor
        from b300_core.gateway_lease import GatewayLeaseStore
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(hardware_owner, "load_isolated_gateway_config", return_value=config, create=True), \
                mock.patch.object(gateway_supervisor, "load_isolated_gateway_config", return_value=config, create=True), \
                mock.patch.dict("os.environ", {"B300_GATEWAY_RUNTIME_DIR": "/tmp/ssh-controlled"}):
            self.assertEqual(hardware_owner.default_hardware_owner_path(),
                             config.state_root / "hardware-owner.lock")
            self.assertEqual(gateway_supervisor.gateway_runtime_root(), config.state_root)
            self.assertEqual(GatewayLeaseStore().path, config.state_root / "lease.json")

    def test_local_recovery_command_requires_confirmation_and_quiescent_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.lock"
            code = (
                "import os,sys; from pathlib import Path; "
                "from b300_core.hardware_owner import FileHardwareOwner; "
                "FileHardwareOwner(Path(sys.argv[1])).acquire(); os._exit(0)"
            )
            subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=5)
            owner = FileHardwareOwner(path)
            output = io.StringIO()
            with mock.patch.object(b300_stlink, "DEFAULT_HARDWARE_OWNER", owner), \
                    mock.patch.object(b300_stlink, "openocd_quiescent", return_value=True), \
                    redirect_stdout(output):
                self.assertEqual(b300_stlink.main([
                    "hardware", "recover", "--confirm-hardware-recovery", "--json",
                ]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "ok")
            owner.acquire().release()

    def test_crashed_owner_blocks_new_cli_until_explicit_quiescent_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.lock"
            code = (
                "import os,sys; from pathlib import Path; "
                "from b300_core.hardware_owner import FileHardwareOwner; "
                "FileHardwareOwner(Path(sys.argv[1])).acquire(); os._exit(0)"
            )
            crashed = subprocess.run([sys.executable, "-c", code, str(path)], timeout=5)
            self.assertEqual(crashed.returncode, 0)
            owner = FileHardwareOwner(path)
            with self.assertRaisesRegex(RuntimeError, "RECOVERY_REQUIRED"):
                owner.acquire()
            with self.assertRaisesRegex(RuntimeError, "RECOVERY_REQUIRED"):
                owner.recover(confirm=True, quiescent_probe=lambda: False)
            owner.recover(confirm=True, quiescent_probe=lambda: True)
            owner.acquire().release()

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
