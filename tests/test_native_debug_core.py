import unittest

from b300_core.native_debug_core import (
    NativeCoreUnavailable,
    NativeDebugCoreAdapter,
)


class _FakeNative:
    ABI_VERSION = 1

    @staticmethod
    def decode_fixed_width(payload, channel, timestamp_ns, source_id):
        count = len(payload) // 4
        events = []
        for index in range(count):
            offset = index * 4
            events.append(
                {
                    "timestamp_ns": timestamp_ns + index,
                    "source_id": source_id,
                    "channel": channel,
                    "type": 6,
                    "value": int.from_bytes(payload[offset : offset + 4], "little"),
                }
            )
        return {"consumed": count * 4, "events": events}


class _OverConsumingNative:
    ABI_VERSION = 1

    @staticmethod
    def decode_fixed_width(payload, channel, timestamp_ns, source_id):
        return {"consumed": len(payload) + 1, "events": []}


class NativeDebugCoreAdapterTests(unittest.TestCase):
    def test_off_mode_uses_python_fallback(self):
        adapter = NativeDebugCoreAdapter(mode="off", native_module=_FakeNative())
        self.assertEqual(adapter.backend, "python")
        result = adapter.decode_fixed_width(
            b"\x01\x00\x00\x00\x02\x00\x00\x00\xff",
            channel=3,
            timestamp_ns=100,
            source_id=9,
        )
        self.assertEqual(result.consumed, 8)
        self.assertEqual([event.value for event in result.events], [1, 2])
        self.assertEqual([event.timestamp_ns for event in result.events], [100, 101])

    def test_native_and_python_paths_have_parity(self):
        payload = b"\x78\x56\x34\x12\x01\x02\x03\x04\xaa\xbb"
        fallback = NativeDebugCoreAdapter(mode="off")
        native = NativeDebugCoreAdapter(mode="on", native_module=_FakeNative())

        expected = fallback.decode_fixed_width(
            payload,
            channel=7,
            timestamp_ns=1_000,
            source_id=42,
        )
        actual = native.decode_fixed_width(
            payload,
            channel=7,
            timestamp_ns=1_000,
            source_id=42,
        )
        self.assertEqual(actual, expected)
        self.assertEqual(native.backend, "native")

    def test_on_mode_fails_closed_for_incompatible_module(self):
        with self.assertRaises(NativeCoreUnavailable):
            NativeDebugCoreAdapter(mode="on", native_module=object())

    def test_native_overconsume_fails_closed(self):
        adapter = NativeDebugCoreAdapter(mode="on", native_module=_OverConsumingNative())
        with self.assertRaises(RuntimeError):
            adapter.decode_fixed_width(
                b"\x00\x00\x00\x00",
                channel=1,
                timestamp_ns=0,
                source_id=0,
            )

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            NativeDebugCoreAdapter(mode="turbo")


if __name__ == "__main__":
    unittest.main()
