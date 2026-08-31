from __future__ import annotations

import struct
import unittest
from types import SimpleNamespace

from b300_core.live_monitor import (
    DWT_PCSR_ADDRESS, LiveWatch, _decode_watch, run_live_monitor, validate_live_request,
)
from b300_core.offline_symbols import ElfSymbol, SourceLocation


class FakeSymbols:
    def __init__(self):
        self.source_calls = []
    def symbol(self, name):
        if name == "xTickCount":
            return ElfSymbol(0x20000030, 4, "d", name)
        if name == "flag":
            return ElfSymbol(0x20000035, 1, "D", name)
        if name == "invalid_gap":
            return ElfSymbol(0x18000000, 4, "D", name)
        raise ValueError(name)
    def source_location(self, pc):
        self.source_calls.append(pc)
        return SourceLocation(pc, "func_%X" % pc, "main.c", 42)


class FakeTcl:
    def __init__(self, rows, states=None):
        self.rows = list(rows)
        self.states = list(states or ["running"] * 100)
        self.requests = []
    def wait_target_state(self):
        return self.states.pop(0) if self.states else "running"
    def read_word_addresses(self, addresses):
        self.requests.append(tuple(addresses))
        return self.rows.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def wait(self, seconds):
        self.now += max(0.0, seconds)
        return False


class LiveMonitorTests(unittest.TestCase):
    def test_zero_halt_monitor_anchors_cadence_and_decodes_ram_watch(self):
        symbols = FakeSymbols()
        # PCSR then aligned word at xTickCount.
        rows = [
            (0x08025FDA, 100),
            (0x08024958, 200),
            (0x0802AA8C, 300),
        ]
        tcl = FakeTcl(rows)
        clock = FakeClock()
        samples = []
        summary = run_live_monitor(
            tcl, symbols, interval_seconds=0.5, sample_limit=3,
            watch_specs=("xTickCount:u32",), clock=clock, wait=clock.wait,
            on_sample=samples.append, state_check_every=1,
        )
        self.assertEqual([item.scheduled_elapsed_seconds for item in samples], [0.0, 0.5, 1.0])
        self.assertEqual([item.values[0].value for item in samples], [100, 200, 300])
        self.assertEqual(samples[0].source.function, "func_8025FDA")
        self.assertEqual(summary.samples, 3)
        self.assertEqual(summary.final_target_state, "running")
        self.assertTrue(all(request[0] == DWT_PCSR_ADDRESS for request in tcl.requests))

    def test_monitor_refuses_halted_target_without_resuming(self):
        with self.assertRaisesRegex(RuntimeError, "RUNNING"):
            run_live_monitor(FakeTcl([], states=["halted"]), FakeSymbols(), sample_limit=1)

    def test_monitor_fails_if_target_stops_during_trace(self):
        tcl = FakeTcl([(0x08025FDA,)], states=["running", "halted", "halted"])
        clock = FakeClock()
        with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
            run_live_monitor(
                tcl, FakeSymbols(), interval_seconds=0.5, sample_limit=1,
                clock=clock, wait=clock.wait, state_check_every=1,
            )

    def test_watch_type_validation_and_unaligned_u8_decode(self):
        symbols = FakeSymbols()
        from b300_core.live_monitor import parse_live_watch
        watch = parse_live_watch("flag:u8", symbols)
        self.assertEqual(watch.address, 0x20000035)
        value = _decode_watch(watch, {0x20000034: 0x00007F00})
        self.assertEqual(value.value, 0x7F)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            parse_live_watch("flag:ptr", symbols)

    def test_request_limits_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "0.1..60.0"):
            validate_live_request(0.01, 10, ())
        with self.assertRaisesRegex(ValueError, "100000"):
            validate_live_request(0.1, 100001, ())

    def test_f64_double_read_marks_torn_value_incoherent(self):
        from b300_core.live_monitor import _decode_watch
        import struct
        watch = LiveWatch("speed", "f64", 0x20000000, 8)
        first_bytes = struct.pack("<d", 12.5)
        second_bytes = struct.pack("<d", 13.5)
        first = {
            0x20000000: int.from_bytes(first_bytes[:4], "little"),
            0x20000004: int.from_bytes(first_bytes[4:], "little"),
        }
        second = {
            0x20000000: int.from_bytes(second_bytes[:4], "little"),
            0x20000004: int.from_bytes(second_bytes[4:], "little"),
        }
        torn = _decode_watch(watch, first, second)
        self.assertFalse(torn.coherent)
        self.assertIsNone(torn.value)
        self.assertIsNotNone(torn.verification_raw_hex)
        stable = _decode_watch(watch, first, first)
        self.assertTrue(stable.coherent)
        self.assertEqual(stable.value, 12.5)

    def test_watch_must_be_inside_real_f407_ram_ranges(self):
        from b300_core.live_monitor import parse_live_watch
        with self.assertRaisesRegex(ValueError, "CCM/SRAM"):
            parse_live_watch("invalid_gap:u32", FakeSymbols())

    def test_watch_syntax_is_validated_before_symbol_lookup(self):
        from b300_core.live_monitor import validate_live_watch_specs
        with self.assertRaisesRegex(ValueError, "NAME:TYPE"):
            validate_live_watch_specs(("xTickCount",))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_live_watch_specs(("xTickCount:pointer",))
        with self.assertRaisesRegex(ValueError, "At most 16"):
            validate_live_watch_specs(tuple("v%d:u32" % i for i in range(17)))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            validate_live_watch_specs(("xTickCount:u32", "xTickCount:i32"))

    def test_save_and_load_watch_preset_roundtrip(self):
        from b300_core.live_monitor import save_watch_preset, load_watch_preset
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            preset_path = Path(tmpdir) / "motor_watch.json"
            specs = ("xTickCount:u32", "bRUN:u8", "v_current:f64")
            plot_flags = {"xTickCount": True, "bRUN": False, "v_current": True}
            saved = save_watch_preset(
                preset_path, specs, name="Motor Diagnostics",
                interval_seconds=0.2, sample_limit=500, plot_flags=plot_flags,
            )
            self.assertEqual(saved, preset_path)
            self.assertTrue(preset_path.is_file())

            loaded = load_watch_preset(preset_path)
            self.assertEqual(loaded["name"], "Motor Diagnostics")
            self.assertEqual(loaded["interval_seconds"], 0.2)
            self.assertEqual(loaded["sample_limit"], 500)
            self.assertEqual(loaded["specs"], ("xTickCount:u32", "bRUN:u8", "v_current:f64"))
            self.assertEqual(loaded["plot_flags"], {"xTickCount": True, "bRUN": False, "v_current": True})
            self.assertEqual(len(loaded["watches"]), 3)
            self.assertEqual(loaded["watches"][0], {"name": "xTickCount", "type": "u32", "plot": True})
            self.assertEqual(loaded["watches"][1], {"name": "bRUN", "type": "u8", "plot": False})

    def test_load_watch_preset_supports_simple_array_format(self):
        from b300_core.live_monitor import load_watch_preset
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            preset_path = Path(tmpdir) / "simple.json"
            preset_path.write_text(json.dumps(["xTickCount:u32", "bRUN:u8"]), encoding="utf-8")
            loaded = load_watch_preset(preset_path)
            self.assertEqual(loaded["specs"], ("xTickCount:u32", "bRUN:u8"))
            self.assertEqual(loaded["plot_flags"], {"xTickCount": True, "bRUN": True})

    def test_load_watch_preset_rejects_invalid_json_or_specs(self):
        from b300_core.live_monitor import load_watch_preset
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_path = Path(tmpdir) / "corrupt.json"
            corrupt_path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON"):
                load_watch_preset(corrupt_path)

            invalid_spec_path = Path(tmpdir) / "invalid.json"
            invalid_spec_path.write_text('["xTickCount:invalid_type"]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                load_watch_preset(invalid_spec_path)


if __name__ == "__main__":
    unittest.main()

