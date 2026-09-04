from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


def _find_module(build_dir: Path) -> Path:
    candidates = sorted(
        path
        for path in build_dir.rglob("_b300_debug_core*")
        if path.suffix.lower() in {".so", ".pyd", ".dylib"}
    )
    if not candidates:
        raise SystemExit(f"native module not found under {build_dir}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()

    module_path = _find_module(args.build_dir.resolve())
    sys.path.insert(0, str(module_path.parent))
    module = importlib.import_module("_b300_debug_core")
    if int(getattr(module, "ABI_VERSION", -1)) != 1:
        raise SystemExit("unexpected native ABI version")

    result = module.decode_fixed_width(
        b"\x78\x56\x34\x12\x01\x00\x00\x00\xff",
        7,
        1000,
        42,
    )
    assert result["consumed"] == 8
    assert len(result["events"]) == 2
    assert result["events"][0]["timestamp_ns"] == 1000
    assert result["events"][0]["source_id"] == 42
    assert result["events"][0]["channel"] == 7
    assert result["events"][0]["value"] == 0x12345678
    assert result["events"][1]["timestamp_ns"] == 1001
    assert result["events"][1]["value"] == 1
    print(f"native bridge smoke OK: {module_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
