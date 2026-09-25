from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from scripts import activate_isolated_gateway as activation


class FakeRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command, timeout=30):
        self.commands.append(tuple(command))
        stdout = "active\n" if "is-active" in command else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class IdleProbes:
    def agent_idle(self):
        return True

    def jobs_idle(self):
        return True

    def openocd_quiescent(self):
        return True

    def service_idle(self):
        return True


class ActivationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "candidate.tar.gz"
        self.bundle.write_bytes(b"candidate")
        self.digest = "a" * 64
        self.candidate = self.root / "opt/b300-stlink/candidates/0.24.0-" / self.digest
        self.identities = {
            "agent_uid": 2001, "agent_gid": 2001, "probe_gid": 2002,
            "upload_gid": 2003, "operator_access_gid": 2004,
            "operator_uid": 1000, "operator_gid": 1000,
        }
        self.plan = SimpleNamespace(
            ready=True,
            candidate={
                "path": str(self.bundle), "sha256": self.digest,
                "version": "0.24.0",
            },
            rollback_inventory={
                "services": {
                    "legacy_user": {"enabled": True, "active": True},
                    "system_agent": {"enabled": False, "active": False},
                    "ingress_mount": {"enabled": False, "active": False},
                },
            },
        )
        self.runner = FakeRunner()

    def _stager(self, plan, bundle, digest, **kwargs):
        candidate = self.candidate
        candidate.mkdir(parents=True, exist_ok=True)
        exe = candidate / "b300-stlink"
        exe.write_bytes(b"#!/bin/sh\n")
        exe.chmod(0o755)
        vendor = candidate / "vendor/openocd/bin"
        vendor.mkdir(parents=True)
        tool = vendor / "openocd"
        tool.write_bytes(b"openocd")
        tool.chmod(0o755)
        systemd = candidate / "systemd"
        systemd.mkdir()
        (systemd / activation.installer.SYSTEM_UNIT).write_text(
            "[Service]\nExecStart=/opt/b300-stlink/bin/b300-stlink debug gateway-agent --managed-child --json\n",
            encoding="utf-8",
        )
        (systemd / "b300-stlink-ingress.mount.rendered").write_text(
            "[Mount]\nWhere=/var/spool/b300-stlink/ingress\n", encoding="utf-8"
        )
        return {"state": "STAGED", "candidate_dir": str(candidate),
                "journal_path": str(self.root / "stage.json"), "reused": False}

    def test_prepare_installs_only_pending_marker_before_boundary_verification(self):
        result = activation._prepare(
            self.plan, self.bundle, self.digest, host=object(),
            root=self.root, runner=self.runner,
            identities_provider=lambda: dict(self.identities),
            stager=self._stager, fsync_dir=lambda path: None,
            effective_uid=lambda: 0, system_name="linux",
        )
        self.assertEqual(result["state"], "PREPARED_PENDING_BOUNDARY")
        marker = self.root / "etc/b300-stlink/isolated-gateway.json"
        record = json.loads(marker.read_text(encoding="utf-8"))
        self.assertFalse(record["flash_enabled"])
        self.assertEqual(record["operator_uid"], 1000)
        self.assertEqual(record["operator_gid"], 2004)
        self.assertTrue((self.root / "opt/b300-stlink/bin/b300-stlink").is_file())
        self.assertTrue((self.root / "etc/systemd/system" /
                         activation.installer.SYSTEM_UNIT).is_file())
        self.assertEqual(
            (self.root / "etc/udev/rules.d/99-b300-agent.rules").read_bytes(),
            activation.UDEV_RULE,
        )
        receipt = json.loads((self.root / "opt/b300-stlink/ACTIVATION-RECEIPT.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "PREPARED_PENDING_BOUNDARY")
        commands = self.runner.commands
        legacy_stop = commands.index(activation._legacy_service_command(
            "aubot", "stop", activation.installer.SYSTEM_UNIT))
        system_start = commands.index(activation._service_command(
            "enable", "--now", activation.installer.SYSTEM_UNIT))
        self.assertLess(legacy_stop, system_start)
        self.assertNotIn(b'"flash_enabled":true', marker.read_bytes().lower())

    def test_prepare_persists_rollback_receipt_before_active_runtime_change(self):
        with mock.patch.object(
                activation, "_copy_runtime",
                side_effect=activation.ActivationError("INJECTED_FAILURE")):
            with self.assertRaises(activation.ActivationError) as captured:
                activation._prepare(
                    self.plan, self.bundle, self.digest, host=object(),
                    root=self.root, runner=self.runner,
                    identities_provider=lambda: dict(self.identities),
                    stager=self._stager, fsync_dir=lambda path: None,
                    effective_uid=lambda: 0, system_name="linux",
                )
        self.assertEqual(captured.exception.reason_code, "INJECTED_FAILURE")
        receipt = self.root / "opt/b300-stlink/ACTIVATION-RECEIPT.json"
        self.assertTrue(receipt.is_file())
        record = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "PREPARING")
        self.assertFalse(
            (self.root / "etc/b300-stlink/isolated-gateway.json").exists())
        self.assertEqual(self.runner.commands, [])

    def test_prepare_never_uses_flash_or_openocd_commands(self):
        activation._prepare(
            self.plan, self.bundle, self.digest, host=object(),
            root=self.root, runner=self.runner,
            identities_provider=lambda: dict(self.identities),
            stager=self._stager, fsync_dir=lambda path: None,
            effective_uid=lambda: 0, system_name="linux",
        )
        flattened = "\n".join(" ".join(command) for command in self.runner.commands).lower()
        self.assertNotIn("flash", flattened)
        self.assertNotIn("openocd", flattened)
        self.assertNotIn("mass_erase", flattened)
        self.assertNotIn("option", flattened)

    def test_activate_enables_only_after_boundary_verifier_and_capability(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({
            "schema_version": 1, "status": "PREPARED_PENDING_BOUNDARY",
            "probe_serial": None,
        }), encoding="utf-8")
        receipt.chmod(0o600)
        state = self.root / "state"
        state.mkdir()
        (state / "agent-status.json").write_text(json.dumps({
            "state": "IDLE",
            "capabilities": ["remote_application_flash_isolated_v1"],
        }), encoding="utf-8")
        calls = []

        def verify(**kwargs):
            calls.append(kwargs)
            return {"state": "ACTIVE", "verified": True}

        result = activation._activate(
            probes=IdleProbes(), boundary_verifier=verify,
            receipt_path=receipt, state_root=state,
            fsync_dir=lambda path: None,
            effective_uid=lambda: 0, system_name="linux",
        )
        self.assertEqual(result["state"], "ACTIVE")
        self.assertTrue(result["flash_enabled"])
        self.assertEqual(len(calls), 1)
        stored = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "ACTIVE")

    def test_activate_refuses_when_capability_never_becomes_ready(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({
            "schema_version": 1, "status": "PREPARED_PENDING_BOUNDARY",
            "probe_serial": None,
        }), encoding="utf-8")
        receipt.chmod(0o600)
        state = self.root / "state"
        state.mkdir()
        (state / "agent-status.json").write_text(json.dumps({
            "state": "IDLE", "capabilities": [],
        }), encoding="utf-8")

        original_wait = activation._wait_isolated_capability
        try:
            activation._wait_isolated_capability = lambda *args, **kwargs: (
                (_ for _ in ()).throw(activation.ActivationError(
                    "ISOLATED_CAPABILITY_NOT_READY"))
            )
            with self.assertRaises(activation.ActivationError) as captured:
                activation._activate(
                    probes=IdleProbes(),
                    boundary_verifier=lambda **kwargs: {"state": "ACTIVE"},
                    receipt_path=receipt, state_root=state,
                    fsync_dir=lambda path: None,
                    effective_uid=lambda: 0, system_name="linux",
                )
        finally:
            activation._wait_isolated_capability = original_wait
        self.assertEqual(captured.exception.reason_code,
                         "ISOLATED_CAPABILITY_NOT_READY")
        self.assertEqual(json.loads(receipt.read_text())["status"],
                         "PREPARED_PENDING_BOUNDARY")

    def test_rollback_removes_activation_surface_and_restores_legacy_service(self):
        receipt = self.root / "opt/b300-stlink/ACTIVATION-RECEIPT.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({
            "schema_version": 1, "status": "PREPARED_PENDING_BOUNDARY",
            "operator_name": "aubot",
            "rollback_inventory": {
                "services": {"legacy_user": {"enabled": True, "active": True}},
            },
        }), encoding="utf-8")
        receipt.chmod(0o600)
        for path, payload in (
            (self.root / "etc/b300-stlink/isolated-gateway.json",
             json.dumps({
                 "schema_version": 1, "socket_path": "/run/b300-stlink/agent.sock",
                 "state_root": "/var/lib/b300-stlink/gateway",
                 "ingress_root": "/var/spool/b300-stlink/ingress",
                 "operator_uid": 1000, "operator_gid": 2004,
                 "flash_enabled": False,
             })),
            (self.root / "etc/udev/rules.d/99-b300-agent.rules", "rule"),
            (self.root / "etc/systemd/system" / activation.installer.SYSTEM_UNIT, "unit"),
            (self.root / "etc/systemd/system" / activation.installer.MOUNT_UNIT, "mount"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        active = self.root / "opt/b300-stlink/bin"
        active.mkdir()
        (active / "b300-stlink").write_text("tool", encoding="utf-8")

        result = activation._rollback(
            runner=self.runner, probes=IdleProbes(),
            receipt_path=receipt, root=self.root,
            fsync_dir=lambda path: None,
            effective_uid=lambda: 0, system_name="linux",
        )
        self.assertEqual(result["state"], "ROLLED_BACK")
        self.assertFalse((self.root / "etc/b300-stlink/isolated-gateway.json").exists())
        self.assertFalse(active.exists())
        self.assertIn(
            activation._legacy_service_command(
                "aubot", "start", activation.installer.SYSTEM_UNIT),
            self.runner.commands,
        )
        self.assertEqual(json.loads(receipt.read_text())["status"], "ROLLED_BACK")

    def test_root_linux_is_required(self):
        with self.assertRaises(activation.ActivationError) as captured:
            activation._prepare(
                self.plan, self.bundle, self.digest, host=object(),
                root=self.root, runner=self.runner,
                identities_provider=lambda: dict(self.identities),
                stager=self._stager, fsync_dir=lambda path: None,
                effective_uid=lambda: 1000, system_name="linux",
            )
        self.assertEqual(captured.exception.reason_code, "ROOT_LINUX_REQUIRED")


if __name__ == "__main__":
    unittest.main()
