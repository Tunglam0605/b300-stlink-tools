from __future__ import annotations

import threading
import tempfile
import os
import time
import unittest
from pathlib import Path

from b300_core.debug_service import DebugState
from b300_core.gateway_supervisor import (
    GatewayProcessManager, GatewayStatusStore, GatewaySupervisor,
)
from b300_core.gateway_status import GatewaySnapshot
from b300_core.models import ProbeInfo


PROBE = ProbeInfo("SAFE123", "ST-Link", "test", "usb:1")


class FakeService:
    def __init__(self):
        self._state = DebugState.STOPPED
        self.start_calls = []
        self.stop_calls = 0

    @property
    def state(self):
        return self._state

    def start(self, config, event_sink=None):
        self.start_calls.append(config)
        self._state = DebugState.READY

    def stop(self):
        self.stop_calls += 1
        self._state = DebugState.STOPPED


class GatewaySupervisorTests(unittest.TestCase):
    def test_ready_is_published_only_after_fresh_target_evidence(self) -> None:
        service = FakeService()
        seen = []
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
            snapshot_sink=seen.append,
        )
        result = supervisor.ensure()
        self.assertEqual(result.state, "READY")
        self.assertEqual(result.cpu_state, "running")
        self.assertEqual([item.state for item in seen], ["STARTING", "READY"])
        self.assertEqual(service.start_calls[0].gdb_max_connections, 2)

    def test_two_concurrent_ensure_calls_create_one_debug_owner(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "halted",
        )
        results = []
        threads = [threading.Thread(target=lambda: results.append(supervisor.ensure())) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(service.start_calls), 1)
        self.assertEqual([item.state for item in results], ["READY", "READY"])

    def test_usb_detach_revokes_ready_endpoint_and_stops_old_owner(self) -> None:
        probes = [PROBE]
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: tuple(probes),
            target_state_probe=lambda _config: "running",
        )
        supervisor.ensure()
        probes.clear()
        result = supervisor.observe()
        self.assertEqual(result.state, "DISCONNECTED")
        self.assertIsNone(result.gdb_endpoint)
        self.assertEqual(service.stop_calls, 1)

    def test_maintainer_reopens_gateway_after_same_serial_is_replugged(self) -> None:
        probes = [PROBE]
        services = []

        def make_service():
            service = FakeService()
            services.append(service)
            return service

        supervisor = GatewaySupervisor(
            service_factory=make_service,
            probe_discovery=lambda: tuple(probes),
            target_state_probe=lambda _config: "running",
        )
        self.assertEqual(supervisor.maintain_once().state, "READY")
        first_generation = supervisor.snapshot.generation

        probes.clear()
        disconnected = supervisor.maintain_once()
        self.assertEqual((disconnected.state, disconnected.reason_code),
                         ("DISCONNECTED", "PROBE_REMOVED"))
        self.assertIsNone(disconnected.gdb_endpoint)

        waiting = supervisor.maintain_once()
        self.assertEqual(waiting.state, "WAITING_PROBE")
        probes.append(ProbeInfo("SAFE123", "ST-Link", "test", "usb:1-3"))
        recovered = supervisor.maintain_once()
        self.assertEqual(recovered.state, "READY")
        self.assertGreater(recovered.generation, first_generation)
        self.assertEqual(len(services), 2)

    def test_process_exit_revokes_ready_instead_of_trusting_listener(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
        )
        supervisor.ensure()
        service._state = DebugState.FAILED
        result = supervisor.observe()
        self.assertEqual(result.state, "FAILED")
        self.assertEqual(result.reason_code, "OPENOCD_EXITED")
        self.assertIsNone(result.gdb_endpoint)

    def test_fatal_openocd_log_revokes_ready_and_cleans_owner(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
        )
        supervisor.ensure()
        supervisor._on_openocd_line("Error: libusb_bulk_transfer failed")
        result = supervisor.observe()
        self.assertEqual(result.state, "DISCONNECTED")
        self.assertEqual(result.reason_code, "OPENOCD_HARDWARE_ERROR")
        self.assertEqual(service.stop_calls, 1)

    def test_one_probe_with_unsafe_descriptor_runs_without_adapter_serial(self) -> None:
        unsafe = ProbeInfo(None, "ST-Link", "linux-sysfs", "usb:1", "unsafe_serial")
        supervisor = GatewaySupervisor(
            service_factory=FakeService,
            probe_discovery=lambda: (unsafe,),
            target_state_probe=lambda _config: "running",
        )
        result = supervisor.ensure()
        self.assertEqual(result.state, "READY")
        self.assertIsNone(supervisor._service.start_calls[0].probe.serial)

    def test_process_manager_reuses_live_ready_gateway_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            ready = self._runtime_ready()
            store.write(ready, owner_pid=123)
            spawned = []
            manager = GatewayProcessManager(
                store=store, process_alive=lambda pid: pid == 123,
                process_factory=lambda _command: spawned.append(True),
            )
            self.assertEqual(manager.ensure(("gateway-child",)).state, "READY")
            self.assertEqual(spawned, [])

    def test_process_manager_waits_for_live_nonready_owner_without_spawning_second_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            waiting = GatewaySnapshot.from_record({
                "schema_version": 1, "instance_id": "runtime", "generation": 1,
                "sequence": 2, "state": "WAITING_PROBE", "reason_code": "NO_PROBE",
                "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
                "cpu_state": "unknown", "evidence_age_ms": None,
            })
            store.write(waiting, owner_pid=123)
            spawned = []
            sleeps = []

            def advance(_seconds):
                sleeps.append(True)
                store.write(self._runtime_ready(), owner_pid=123)

            manager = GatewayProcessManager(
                store=store, process_alive=lambda pid: pid == 123,
                process_factory=lambda command: spawned.append(command),
                sleep=advance,
            )
            result = manager.ensure((
                "b300-stlink", "debug", "gateway", "--bind-address", "127.0.0.1",
            ))
            self.assertEqual(result.state, "READY")
            self.assertEqual(spawned, [])
            self.assertTrue(sleeps)

    def test_process_manager_rescan_signals_live_owner_and_waits_for_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            initial = self._runtime_ready()
            store.write(initial, owner_pid=123)

            def advance(_seconds):
                self.assertTrue(store.consume_rescan_requests())
                refreshed = GatewaySnapshot.from_record({
                    **initial.to_record(), "sequence": initial.sequence + 1,
                })
                store.write(refreshed, owner_pid=123)

            manager = GatewayProcessManager(
                store=store, process_alive=lambda pid: pid == 123,
                process_factory=lambda _command: self.fail("rescan spawned a second owner"),
                sleep=advance,
            )
            result = manager.rescan((
                "b300-stlink", "debug", "gateway", "--bind-address", "127.0.0.1",
            ))
            self.assertEqual(result.sequence, initial.sequence + 1)

    def test_process_manager_starts_one_loopback_child_and_waits_for_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            spawned = []

            class Process:
                pid = 456

                @staticmethod
                def poll():
                    return None

            def spawn(command):
                spawned.append(command)
                store.write(self._runtime_ready(), owner_pid=456)
                return Process()

            manager = GatewayProcessManager(
                store=store, process_alive=lambda pid: pid == 456,
                process_factory=spawn, sleep=lambda _seconds: None,
            )
            result = manager.ensure((
                "b300-stlink", "debug", "gateway", "--bind-address", "127.0.0.1",
            ))
            self.assertEqual(result.state, "READY")
            self.assertEqual(len(spawned), 1)
            self.assertIn("127.0.0.1", spawned[0])

    def test_live_process_with_stale_evidence_is_not_reported_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            store.write(self._runtime_ready(), owner_pid=123)
            old = time.time() - 6.0
            os.utime(store.status_path, (old, old))
            manager = GatewayProcessManager(
                store=store, process_alive=lambda pid: pid == 123,
                process_factory=lambda _command: None,
            )
            result = manager.status()
            self.assertEqual(result.state, "DISCONNECTED")
            self.assertEqual(result.reason_code, "GATEWAY_HEARTBEAT_STALE")
            self.assertIsNone(result.gdb_endpoint)

    def test_terminal_probe_failure_survives_child_exit_for_client_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            waiting = GatewaySnapshot.from_record({
                "schema_version": 1, "instance_id": "runtime", "generation": 0,
                "sequence": 1, "state": "WAITING_PROBE", "reason_code": "NO_PROBE",
                "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
                "cpu_state": "unknown", "evidence_age_ms": None,
            })
            store.write(waiting, owner_pid=456)
            manager = GatewayProcessManager(store=store, process_alive=lambda _pid: False)
            result = manager.status()
            self.assertEqual(result.state, "WAITING_PROBE")
            self.assertEqual(result.reason_code, "NO_PROBE")

    def test_ensure_returns_child_probe_failure_without_waiting_for_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayStatusStore(Path(directory))
            sleeps = []

            class Process:
                pid = 456

                @staticmethod
                def poll():
                    return 1

            def spawn(_command):
                waiting = GatewaySnapshot.from_record({
                    "schema_version": 1, "instance_id": "runtime", "generation": 0,
                    "sequence": 1, "state": "WAITING_PROBE", "reason_code": "NO_PROBE",
                    "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
                    "cpu_state": "unknown", "evidence_age_ms": None,
                })
                store.write(waiting, owner_pid=456)
                return Process()

            manager = GatewayProcessManager(
                store=store, process_alive=lambda _pid: False,
                process_factory=spawn, sleep=sleeps.append,
            )
            result = manager.ensure((
                "b300-stlink", "debug", "gateway", "--bind-address", "127.0.0.1",
            ))
            self.assertEqual(result.reason_code, "NO_PROBE")
            self.assertEqual(sleeps, [])

    @staticmethod
    def _runtime_ready():
        return GatewaySnapshot.from_record({
            "schema_version": 1, "instance_id": "runtime", "generation": 1,
            "sequence": 2, "state": "READY", "reason_code": "TARGET_VERIFIED",
            "selected_probe": {"serial": "SAFE123", "usb_identity": "usb:1"},
            "gdb_endpoint": "127.0.0.1:3333", "tcl_endpoint": "127.0.0.1:6666",
            "cpu_state": "running", "evidence_age_ms": 0,
        })


if __name__ == "__main__":
    unittest.main()
