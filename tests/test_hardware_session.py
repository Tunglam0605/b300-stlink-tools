from __future__ import annotations

import threading
import unittest

from b300_core.hardware_session import HardwareBusyError, HardwareMode, HardwareSessionManager
from b300_core.models import ProbeRef
from b300_core.service import B300Service


class HardwareSessionManagerTests(unittest.TestCase):
    def test_acquire_sets_mode_and_releases_to_idle(self) -> None:
        manager = HardwareSessionManager()
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)
        with manager.acquire(HardwareMode.FLASHING, ProbeRef("SAFE123")):
            state = manager.snapshot()
            self.assertEqual(state.mode, HardwareMode.FLASHING)
            self.assertEqual(state.probe_serial, "SAFE123")
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_conflicting_mode_is_rejected_while_busy(self) -> None:
        manager = HardwareSessionManager()
        with manager.acquire(HardwareMode.DEBUGGING, ProbeRef("SAFE123")):
            captured: list[BaseException] = []

            def try_flash() -> None:
                try:
                    with manager.acquire(HardwareMode.FLASHING, ProbeRef("SAFE123")):
                        pass
                except BaseException as error:
                    captured.append(error)

            worker = threading.Thread(target=try_flash)
            worker.start()
            worker.join(timeout=2)

            self.assertEqual(1, len(captured))
            self.assertIsInstance(captured[0], HardwareBusyError)
            self.assertIn("DEBUGGING", str(captured[0]))

    def test_release_happens_after_exception(self) -> None:
        manager = HardwareSessionManager()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with manager.acquire(HardwareMode.FACTORY_PROVISIONING, ProbeRef("SAFE123")):
                raise RuntimeError("boom")
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_same_thread_nested_operation_keeps_outer_mode(self) -> None:
        manager = HardwareSessionManager()
        with manager.acquire(HardwareMode.FLASHING, ProbeRef("SAFE123")):
            with manager.acquire(HardwareMode.READING, ProbeRef("SAFE123")):
                self.assertEqual(manager.snapshot().mode, HardwareMode.FLASHING)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_factory_provisioning_can_nest_reading_on_same_probe(self) -> None:
        manager = HardwareSessionManager()
        probe = ProbeRef("SAFE123")
        with manager.acquire(HardwareMode.FACTORY_PROVISIONING, probe):
            with manager.acquire(HardwareMode.READING, probe):
                self.assertEqual(manager.snapshot().mode, HardwareMode.FACTORY_PROVISIONING)

    def test_nested_mode_escalation_or_different_probe_is_denied(self) -> None:
        manager = HardwareSessionManager()
        probe = ProbeRef("SAFE123")
        with manager.acquire(HardwareMode.FLASHING, probe):
            with self.assertRaisesRegex(HardwareBusyError, "FLASHING"):
                with manager.acquire(HardwareMode.DEBUGGING, probe):
                    pass
            with self.assertRaisesRegex(HardwareBusyError, "different probe"):
                with manager.acquire(HardwareMode.READING, ProbeRef("OTHER456")):
                    pass
        with manager.acquire(HardwareMode.DEBUGGING, probe):
            with self.assertRaisesRegex(HardwareBusyError, "DEBUGGING"):
                with manager.acquire(HardwareMode.FLASHING, probe):
                    pass
            with self.assertRaisesRegex(HardwareBusyError, "DEBUGGING"):
                with manager.acquire(HardwareMode.READING, probe):
                    pass
        with manager.acquire(HardwareMode.READING, probe):
            with self.assertRaisesRegex(HardwareBusyError, "READING"):
                with manager.acquire(HardwareMode.FLASHING, probe):
                    pass

    def test_inner_exception_does_not_release_outer_session(self) -> None:
        manager = HardwareSessionManager()
        probe = ProbeRef("SAFE123")
        with manager.acquire(HardwareMode.FLASHING, probe):
            with self.assertRaisesRegex(RuntimeError, "inner"):
                with manager.acquire(HardwareMode.READING, probe):
                    raise RuntimeError("inner")
            self.assertEqual(manager.snapshot().mode, HardwareMode.FLASHING)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_detached_debug_lease_can_be_released_from_another_thread(self) -> None:
        manager = HardwareSessionManager()
        acquired = []

        def start_worker() -> None:
            acquired.append(manager.acquire_debugging(ProbeRef("DEBUG123")))

        worker = threading.Thread(target=start_worker)
        worker.start()
        worker.join(timeout=1)
        self.assertEqual(manager.snapshot().mode, HardwareMode.DEBUGGING)

        release_worker = threading.Thread(target=acquired[0].release)
        release_worker.start()
        release_worker.join(timeout=1)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_other_thread_is_blocked_until_owner_releases(self) -> None:
        manager = HardwareSessionManager()
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def owner() -> None:
            with manager.acquire(HardwareMode.READING, ProbeRef("SAFE123")):
                entered.set()
                release.wait(timeout=2)

        thread = threading.Thread(target=owner)
        thread.start()
        entered.wait(timeout=2)
        try:
            with manager.acquire(HardwareMode.DEBUGGING, ProbeRef("SAFE123")):
                pass
        except HardwareBusyError as error:
            errors.append(error)
        finally:
            release.set()
            thread.join(timeout=2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_service_obeys_injected_debug_session(self) -> None:
        class NoopRunner:
            def run(self, *args, **kwargs):
                self.fail("runner must not be called while session is busy")

            @staticmethod
            def fail(message: str) -> None:
                raise AssertionError(message)

        manager = HardwareSessionManager()
        service = B300Service(
            runner=NoopRunner(), executable="openocd", session_manager=manager
        )
        entered = threading.Event()
        release = threading.Event()

        def debug_owner() -> None:
            with manager.acquire(HardwareMode.DEBUGGING, ProbeRef("SAFE123")):
                entered.set()
                release.wait(timeout=2)

        owner = threading.Thread(target=debug_owner)
        owner.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            with self.assertRaisesRegex(HardwareBusyError, "DEBUGGING"):
                service.inspect_target(ProbeRef("SAFE123"))
        finally:
            release.set()
            owner.join(timeout=2)


    def test_monitoring_detached_lease_is_distinct_and_blocks_competing_operations(self):
        manager = HardwareSessionManager()
        probe = ProbeRef("MON123")
        lease = manager.acquire_monitoring(probe)
        self.assertEqual(manager.snapshot().mode, HardwareMode.MONITORING)
        self.assertEqual(manager.snapshot().probe_serial, "MON123")
        with self.assertRaisesRegex(HardwareBusyError, "MONITORING"):
            with manager.acquire(HardwareMode.FLASHING, probe):
                pass
        with self.assertRaisesRegex(HardwareBusyError, "MONITORING"):
            manager.acquire_debugging(probe)
        lease.release()
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

if __name__ == "__main__":
    unittest.main()
