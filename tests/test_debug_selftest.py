from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.debug_selftest import run_loopback_debug_selftest
from b300_core.elf_matcher import SymbolMatchResult
from b300_core.models import ProbeRef


class FakeService:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.config = None

    def start(self, config):
        self.config = config
        self.started = True

    def stop(self):
        self.stopped = True


class FakeTcl:
    def __init__(self, state="running"):
        self.state = state
        self.read_calls = []

    def wait_target_state(self):
        return self.state

    def read_words(self, address, count):
        self.read_calls.append((address, count))
        return (0,) * count


class FakeSession:
    def __init__(self, tcl):
        self.tcl = tcl
        self.started = False
        self.stopped = False
        self.break_calls = []
        self.watch_calls = []

    def start_external(self, **kwargs):
        self.started = True
        self.start_kwargs = kwargs
        return SimpleNamespace(state="CONNECTED")

    def inspect(self, frames):
        return SimpleNamespace(frame=SimpleNamespace(function="main"))

    def capture_variable(self, expression):
        return SimpleNamespace(expression=expression, value="123")

    def break_once(self, location, timeout_seconds):
        self.break_calls.append((location, timeout_seconds))
        return SimpleNamespace(reason="breakpoint-hit")

    def watch_once(self, expression, timeout_seconds):
        self.watch_calls.append((expression, timeout_seconds))
        return SimpleNamespace(reason="watchpoint-trigger")

    def stop(self):
        self.stopped = True


class DebugSelfTestTests(unittest.TestCase):
    def _symbols(self, directory):
        path = Path(directory) / "firmware.axf"
        path.write_bytes(b"ELF")
        return path

    def test_selftest_verifies_symbols_before_external_attach_and_passes(self):
        service = FakeService()
        tcl = FakeTcl("running")
        session = FakeSession(tcl)
        with tempfile.TemporaryDirectory() as directory:
            symbols = self._symbols(directory)
            matched = SymbolMatchResult(symbols.resolve(), True, 4, 4, 1.0, "ok")
            with mock.patch("b300_core.debug_selftest.match_symbol_file", return_value=matched) as matcher:
                report = run_loopback_debug_selftest(
                    probe=ProbeRef("SAFE"), symbol_file=symbols, service=service, session=session,
                    tcl_factory=lambda _endpoint: tcl, port_probe=lambda _host, _port: True,
                    expression="xTickCount", location="main",
                )
        self.assertTrue(report.passed)
        self.assertEqual(report.conclusion, "PASS")
        matcher.assert_called_once()
        self.assertTrue(session.started)
        self.assertTrue(session.stopped)
        self.assertTrue(service.stopped)
        self.assertIn("SYMBOLS_MATCH_FLASH", [item.code for item in report.checks])
        self.assertEqual(report.initial_target_state, "running")
        self.assertEqual(report.final_target_state, "running")

    def test_symbol_mismatch_fails_closed_before_external_client_attach(self):
        service = FakeService()
        tcl = FakeTcl("running")
        session = FakeSession(tcl)
        with tempfile.TemporaryDirectory() as directory:
            symbols = self._symbols(directory)
            mismatch = SymbolMatchResult(symbols.resolve(), False, 1, 4, 0.25, "mismatch")
            with mock.patch("b300_core.debug_selftest.match_symbol_file", return_value=mismatch):
                report = run_loopback_debug_selftest(
                    probe=ProbeRef("SAFE"), symbol_file=symbols, service=service, session=session,
                    tcl_factory=lambda _endpoint: tcl, port_probe=lambda _host, _port: True,
                )
        self.assertFalse(report.passed)
        self.assertEqual(report.conclusion, "FAILED")
        self.assertFalse(session.started)
        self.assertTrue(session.stopped)
        self.assertTrue(service.stopped)
        failure = next(item for item in report.checks if item.code == "SELFTEST_EXECUTION_FAILED")
        self.assertIn("does not match Application Flash", failure.message)

    def test_initially_halted_target_skips_transient_break_and_watch(self):
        service = FakeService()
        tcl = FakeTcl("halted")
        session = FakeSession(tcl)
        with tempfile.TemporaryDirectory() as directory:
            symbols = self._symbols(directory)
            matched = SymbolMatchResult(symbols.resolve(), True, 4, 4, 1.0, "ok")
            with mock.patch("b300_core.debug_selftest.match_symbol_file", return_value=matched):
                report = run_loopback_debug_selftest(
                    probe=ProbeRef("SAFE"), symbol_file=symbols, service=service, session=session,
                    tcl_factory=lambda _endpoint: tcl, port_probe=lambda _host, _port: True,
                    expression="xTickCount", location="main",
                )
        self.assertTrue(report.passed)
        self.assertEqual(report.conclusion, "PASS_WITH_LIMITS")
        self.assertEqual(session.break_calls, [])
        self.assertEqual(session.watch_calls, [])
        codes = {item.code for item in report.checks}
        self.assertIn("BREAK_SKIPPED_HALTED", codes)
        self.assertIn("WATCH_SKIPPED_HALTED", codes)
        self.assertEqual(report.final_target_state, "halted")


if __name__ == "__main__":
    unittest.main()
