from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.live_session import (
    ClientLiveMonitorConfig, LiveMonitorSession, LocalLiveMonitorConfig,
)
from b300_core.models import ProbeRef
from b300_core.offline_symbols import ElfSymbol, SourceLocation


class FakeService:
    def __init__(self, executable=None):
        self.executable = executable
        self.started = []
        self.stopped = 0
    def start(self, probe, tcl_port):
        self.started.append((probe, tcl_port))
    def stop(self):
        self.stopped += 1


class FakeTunnel:
    instances = []
    def __init__(self, config):
        self.config = config
        self.started = 0
        self.stopped = 0
        type(self).instances.append(self)
    def start(self):
        self.started += 1
        return "OpenOCD forwarded"
    def stop(self):
        self.stopped += 1


class FakeTcl:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.tick = 100
    def read_words(self, address, count=1):
        return tuple(0x08010000 + index * 4 for index in range(count))
    def read_word_addresses(self, addresses):
        self.tick += 1
        result = []
        for address in addresses:
            if address == 0xE000101C:
                result.append(0x08025FDA)
            elif address == 0x20000030:
                result.append(self.tick)
            else:
                result.append(0)
        return tuple(result)
    def wait_target_state(self):
        return "running"


class FakeSymbolTable:
    instances = []
    def __init__(self, path):
        self.path = Path(path)
        self.closed = False
        type(self).instances.append(self)
    def symbol(self, name):
        if name != "xTickCount":
            raise ValueError(name)
        return ElfSymbol(0x20000030, 4, "d", name)
    def source_location(self, pc):
        return SourceLocation(pc, "vApplicationIdleHook", "main.c", 87)
    def close(self):
        self.closed = True


class LiveMonitorSessionTests(unittest.TestCase):
    def setUp(self):
        FakeTunnel.instances = []
        FakeSymbolTable.instances = []

    def make_symbols(self, directory):
        path = Path(directory) / "firmware.axf"
        path.write_bytes(b"fake")
        return path

    def matched(self, path):
        return SimpleNamespace(path=Path(path), matched=True, reason="match")

    def test_local_session_owns_service_and_releases_it_on_close(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_session.find_matching_symbol_file") as matcher:
            symbols = self.make_symbols(directory)
            matcher.return_value = (self.matched(symbols), ())
            session = LiveMonitorSession(
                openocd_executable="openocd", service_factory=FakeService,
                tcl_factory=FakeTcl, symbol_table_factory=FakeSymbolTable,
            )
            info = session.start_local(LocalLiveMonitorConfig(
                ProbeRef("ABC"), symbols, interval_seconds=0.1, sample_limit=2,
                watch_specs=("xTickCount:u32",), tcl_port=16666,
            ))
            self.assertTrue(session.active)
            self.assertEqual(info.role, "local")
            self.assertEqual(info.initial_target_state, "running")
            service = session._service
            summary = session.run()
            self.assertEqual(summary.samples, 2)
            self.assertEqual(summary.final_target_state, "running")
            session.close()
            self.assertFalse(session.active)
            self.assertEqual(service.stopped, 1)
            self.assertTrue(FakeSymbolTable.instances[-1].closed)

    def test_cancel_interrupts_long_interval_without_waiting_for_interval(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_session.find_matching_symbol_file") as matcher:
            symbols = self.make_symbols(directory)
            matcher.return_value = (self.matched(symbols), ())
            session = LiveMonitorSession(
                service_factory=FakeService, tcl_factory=FakeTcl,
                symbol_table_factory=FakeSymbolTable,
            )
            session.start_local(LocalLiveMonitorConfig(
                ProbeRef("ABC"), symbols, interval_seconds=60.0, sample_limit=None,
                watch_specs=("xTickCount:u32",),
            ))
            first_sample = threading.Event()
            result = {}
            def worker():
                result["summary"] = session.run(lambda sample: first_sample.set())
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(first_sample.wait(1.0))
            started = time.monotonic()
            session.cancel()
            thread.join(1.0)
            elapsed = time.monotonic() - started
            self.assertFalse(thread.is_alive())
            self.assertLess(elapsed, 0.5)
            self.assertTrue(result["summary"].cancelled)
            self.assertEqual(result["summary"].samples, 1)
            self.assertEqual(result["summary"].final_target_state, "running")
            session.close()

    def test_client_session_uses_tcl_only_tunnel_and_no_local_service(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_session.find_matching_symbol_file") as matcher:
            symbols = self.make_symbols(directory)
            matcher.return_value = (self.matched(symbols), ())
            session = LiveMonitorSession(
                tunnel_factory=FakeTunnel, tcl_factory=FakeTcl,
                symbol_table_factory=FakeSymbolTable, port_allocator=lambda preferred: 17666,
            )
            info = session.start_client(ClientLiveMonitorConfig(
                "gateway.local", "automation", symbols, interval_seconds=0.5,
                sample_limit=1, watch_specs=("xTickCount:u32",),
            ))
            self.assertEqual(info.role, "client")
            self.assertEqual(info.transport, "ssh-tcl-local-forwarding")
            self.assertEqual(info.tcl_endpoint, "127.0.0.1:17666")
            tunnel = FakeTunnel.instances[-1]
            self.assertEqual(tunnel.config.local_tcl_port, 17666)
            self.assertEqual(tunnel.config.gateway_tcl_port, 6666)
            self.assertIsNone(session._service)
            self.assertEqual(session.run().samples, 1)
            session.close()
            self.assertEqual(tunnel.stopped, 1)

    def test_client_symbol_root_can_resolve_unique_matching_axf(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_session.discover_symbol_files") as discover, \
                mock.patch("b300_core.live_session.find_matching_symbol_file") as matcher:
            root = Path(directory)
            symbols = self.make_symbols(directory)
            discover.return_value = (symbols,)
            matcher.return_value = (self.matched(symbols), ())
            session = LiveMonitorSession(
                tunnel_factory=FakeTunnel, tcl_factory=FakeTcl,
                symbol_table_factory=FakeSymbolTable, port_allocator=lambda preferred: 17666,
            )
            info = session.start_client(ClientLiveMonitorConfig(
                "gateway.local", "automation", symbols=None, interval_seconds=0.5,
                sample_limit=1, watch_specs=("xTickCount:u32",), symbol_roots=(root,),
            ))
            self.assertEqual(Path(info.symbols), symbols)
            discover.assert_called_once_with((root.resolve(),), max_files=128, max_depth=8)
            session.close()

    def test_invalid_config_fails_before_transport_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            symbols = self.make_symbols(directory)
            called = []
            session = LiveMonitorSession(
                service_factory=lambda **kwargs: called.append(True) or FakeService(),
            )
            with self.assertRaisesRegex(ValueError, "0.1..60.0"):
                session.start_local(LocalLiveMonitorConfig(
                    ProbeRef("ABC"), symbols, interval_seconds=0.01, sample_limit=1,
                ))
            self.assertEqual(called, [])

    def test_start_refuses_halted_target_and_cleans_transport(self):
        class HaltedTcl(FakeTcl):
            def wait_target_state(self):
                return "halted"
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_session.find_matching_symbol_file") as matcher:
            symbols = self.make_symbols(directory)
            matcher.return_value = (self.matched(symbols), ())
            service = FakeService()
            session = LiveMonitorSession(
                service_factory=lambda executable=None: service, tcl_factory=HaltedTcl,
                symbol_table_factory=FakeSymbolTable,
            )
            with self.assertRaisesRegex(RuntimeError, "RUNNING"):
                session.start_local(LocalLiveMonitorConfig(ProbeRef("ABC"), symbols))
            self.assertEqual(service.stopped, 1)
            self.assertFalse(session.active)


if __name__ == "__main__":
    unittest.main()
