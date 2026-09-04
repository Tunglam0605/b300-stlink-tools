#!/usr/bin/env python3
"""PyInstaller-safe B300 GUI entry point."""

from __future__ import annotations

import sys


def _native_core_selftest() -> int:
    from b300_core.native_debug_core import NativeDebugCoreAdapter

    adapter = NativeDebugCoreAdapter(mode="on")
    result = adapter.decode_fixed_width(
        b"\x01\x00\x00\x00\x02\x00\x00\x00",
        channel=7,
        timestamp_ns=123,
        source_id=9,
    )
    values = [event.value for event in result.events]
    if adapter.backend != "native" or result.consumed != 8 or values != [1, 2]:
        raise RuntimeError("packaged native debug-core self-test failed")
    print("B300 NATIVE DEBUG CORE: OK · ABI v1")
    return 0


def main(argv=None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected and selected[0] == "--apply-verified-update":
        from b300_core.update_helper import main as update_helper_main
        return update_helper_main(selected[1:])
    if selected == ["--native-core-selftest"]:
        return _native_core_selftest()
    from b300_gui.__main__ import main as gui_main
    return gui_main(selected)


if __name__ == "__main__":
    raise SystemExit(main())
