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
from b300_core.remote_debug_guard import RemoteDebugGuard


PROBE = ProbeInfo("SAFE123", "ST-Link", "test", "usb:1")


class FakeService:
    def __init__(self):
        self._state = DebugState.STOPPED
        self.start_calls = []
        self.stop_calls = 0
        self.event_sink = None

    @property
    def state(self):
        return self._state

    def start(self, config, event_sink=None):
        self.start_calls.append(config)
        self.event_sink = event_sink
        self._state = DebugState.READY

    def stop(self):
        self.stop_calls += 1
        self._state = DebugState.STOPPED

    def emit(self, line):
        self.event_sink(line)


class FakeTcl:
    def __init__(self, state="running"):
        self.state = state
        self.resume_calls = 0

    def wait_target_state(self):
        return self.state

    def resume_target(self):
        self.resume_calls += 1
        self.state = "running"
        return self.state


class StartupHardwareErrorService(FakeService):
    def start(self, config, event_sink=None):
        super().start(config, event_sink=event_sink)
        event_sink("Error: libusb_bulk_transfer failed")


class GatewaySupervisorTests(unittest.TestCase):
    def test_managed_owner_restores_running_only_after_last_gdb_disconnect(self) -> None:
        service = FakeService()
        tcl = FakeTcl("running")
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: tcl.state,
            remote_guard_factory=lambda _config: RemoteDebugGuard(tcl),
        )
        self.assertEqual(supervisor.ensure().state, "READY")

        service.emit("Info : accepting 'gdb' connection on tcp/3333")
        service.emit("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"
        service.emit("Info : dropped 'gdb' connection")
        self.assertEqual((tcl.state, tcl.resume_calls), ("halted", 0))

        service.emit("Info : dropped 'gdb' connection")
        self.assertEqual((tcl.state, tcl.resume_calls), ("running", 1))

    def test_gdb_activity_events_publish_count_without_stopping_gateway(self) -> None:
        service = FakeService()
        snapshots = []
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
            snapshot_sink=snapshots.append,
            remote_guard_factory=lambda _config: RemoteDebugGuard(FakeTcl("running")),
        )
        supervisor.ensure()
        base_sequence = supervisor.snapshot.sequence

        service.emit("Info : accepting 'gdb' connection on tcp/3333")
        attached = supervisor.snapshot
        service.emit("Info : dropped 'gdb' connection")
        detached = supervisor.snapshot

        self.assertEqual((attached.gdb_connection_count, attached.gdb_activity_generation), (1, 1))
        self.assertTrue(attached.gdb_ever_attached)
        self.assertEqual((detached.gdb_connection_count, detached.gdb_activity_generation), (0, 2))
        self.assertGreater(detached.sequence, base_sequence)
        self.assertEqual(service.stop_calls, 0)

    def test_managed_owner_shutdown_restores_a_previously_running_target(self) -> None:
        service = FakeService()
        tcl = FakeTcl("running")
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: tcl.state,
            remote_guard_factory=lambda _config: RemoteDebugGuard(tcl),
        )
        supervisor.ensure()
        service.emit("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"

        supervisor.stop()

        self.assertEqual((tcl.state, tcl.resume_calls), ("running", 1))
        self.assertEqual(service.stop_calls, 1)

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

    def test_transient_target_probe_failure_reuses_openocd_and_recovers(self) -> None:
        service = FakeService()
        target_states = iter(("running", RuntimeError("GDB attach busy"), "running"))

        def target_state(_config):
            result = next(target_states)
            if isinstance(result, Exception):
                raise result
            return result

        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=target_state,
        )
        self.assertEqual(supervisor.maintain_once().state, "READY")

        unavailable = supervisor.maintain_once()
        self.assertEqual((unavailable.state, unavailable.reason_code),
                         ("DISCONNECTED", "TARGET_UNVERIFIED"))
        self.assertEqual(service.stop_calls, 0)

        recovered = supervisor.maintain_once()
        self.assertEqual((recovered.state, recovered.cpu_state), ("READY", "running"))
        self.assertEqual(len(service.start_calls), 1)
        self.assertEqual(service.stop_calls, 0)

    def test_initial_target_probe_failure_keeps_started_openocd_for_retry(self) -> None:
        service = FakeService()
        target_states = iter((RuntimeError("target settling"), "halted"))

        def target_state(_config):
            result = next(target_states)
            if isinstance(result, Exception):
                raise result
            return result

        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=target_state,
        )
        first = supervisor.ensure()
        self.assertEqual((first.state, first.reason_code),
                         ("DISCONNECTED", "TARGET_UNVERIFIED"))
        self.assertEqual(service.stop_calls, 0)

        recovered = supervisor.ensure()
        self.assertEqual((recovered.state, recovered.cpu_state), ("READY", "halted"))
        self.assertEqual(len(service.start_calls), 1)

    def test_fatal_openocd_log_revokes_ready_and_cleans_owner(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
        )
        supervisor.ensure()
        supervisor._on_openocd_line("Error: libusb_bulk_transfer failed")
        result = supervisor.maintain_once()
        self.assertEqual(result.state, "DISCONNECTED")
        self.assertEqual(result.reason_code, "OPENOCD_HARDWARE_ERROR")
        self.assertEqual(service.stop_calls, 1)

    def test_gdb_memory_error_does_not_restart_healthy_openocd(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
        )
        initial = supervisor.ensure()

        supervisor._on_openocd_line(
            "Error: Failed to read memory at 0x20000030 while target is running"
        )
        result = supervisor.maintain_once()

        self.assertEqual((result.state, result.reason_code),
                         ("READY", "TARGET_VERIFIED"))
        self.assertEqual(result.generation, initial.generation)
        self.assertEqual(service.stop_calls, 0)

    def test_all_public_cycles_clean_hardware_error_before_any_recreate(self) -> None:
        for action in ("ensure", "rescan", "maintain_once"):
            services = []

            def make_service():
                service = FakeService()
                services.append(service)
                return service

            supervisor = GatewaySupervisor(
                service_factory=make_service,
                probe_discovery=lambda: (PROBE,),
                target_state_probe=lambda _config: "running",
            )
            supervisor.ensure()
            supervisor._on_openocd_line("Error: swd fault")

            result = getattr(supervisor, action)()

            self.assertEqual((result.state, result.reason_code),
                             ("DISCONNECTED", "OPENOCD_HARDWARE_ERROR"))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0].stop_calls, 1)

    def test_startup_hardware_error_takes_precedence_over_target_probe_failure(self) -> None:
        service = StartupHardwareErrorService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: (_ for _ in ()).throw(
                RuntimeError("target unavailable")
            ),
        )

        result = supervisor.ensure()

        self.assertEqual((result.state, result.reason_code),
                         ("DISCONNECTED", "OPENOCD_HARDWARE_ERROR"))
        self.assertEqual(service.stop_calls, 1)
        self.assertIsNone(supervisor._service)

    def test_hardware_error_during_target_retry_stops_retained_owner(self) -> None:
        service = FakeService()
        target_states = iter(("running", RuntimeError("GDB busy")))

        def target_state(_config):
            result = next(target_states)
            if isinstance(result, Exception):
                raise result
            return result

        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=target_state,
        )
        supervisor.ensure()
        supervisor.observe()
        supervisor._on_openocd_line("Error: target not examined")

        result = supervisor.maintain_once()

        self.assertEqual((result.state, result.reason_code),
                         ("DISCONNECTED", "OPENOCD_HARDWARE_ERROR"))
        self.assertEqual(service.stop_calls, 1)
        self.assertIsNone(supervisor._service)

    def test_hardware_error_latched_inside_probe_never_publishes_ready(self) -> None:
        service = FakeService()
        snapshots = []
        supervisor = None

        def target_state(_config):
            supervisor._on_openocd_line("Error: swd fault during target probe")
            return "running"

        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=target_state,
            snapshot_sink=snapshots.append,
        )

        result = supervisor.ensure()

        self.assertEqual((result.state, result.reason_code),
                         ("DISCONNECTED", "OPENOCD_HARDWARE_ERROR"))
        self.assertFalse(any(item.attach_ready for item in snapshots))
        self.assertEqual(service.stop_calls, 1)

    def test_latched_fault_is_fail_closed_even_before_callback_publishes(self) -> None:
        service = FakeService()
        supervisor = GatewaySupervisor(
            service_factory=lambda: service,
            probe_discovery=lambda: (PROBE,),
            target_state_probe=lambda _config: "running",
        )
        supervisor.ensure()
        supervisor._hardware_error = True

        result = supervisor.maintain_once()

        self.assertEqual((result.state, result.reason_code),
                         ("DISCONNECTED", "OPENOCD_HARDWARE_ERROR"))
        self.assertFalse(result.attach_ready)
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
