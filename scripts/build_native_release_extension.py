#!/usr/bin/env python3
"""Build and verify the optional B300 native Python extension for release packaging."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native" / "b300_debug_core"


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def _pybind_cmake_dir() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pybind11", "--cmakedir"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("pybind11 did not report its CMake package directory.")
    return value


def _candidate_extensions(build_dir: Path) -> list[Path]:
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    rows: list[Path] = []
    for path in build_dir.rglob("_b300_debug_core*"):
        if path.is_file() and any(path.name.endswith(suffix) for suffix in suffixes):
            rows.append(path.resolve())
    return sorted(set(rows))


def _verify_extension(path: Path) -> None:
    spec = importlib.util.spec_from_file_location("_b300_debug_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot create import spec for native extension: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if int(getattr(module, "ABI_VERSION", -1)) != 1:
        raise RuntimeError("Native debug-core ABI mismatch; expected ABI_VERSION=1.")
    result = module.decode_fixed_width(b"\x01\x00\x00\x00\x02\x00\x00\x00", 7, 123, 9)
    if int(result.get("consumed", -1)) != 8:
        raise RuntimeError("Native release smoke test did not consume the expected payload.")
    events = list(result.get("events", ()))
    if len(events) != 2 or [int(row["value"]) for row in events] != [1, 2]:
        raise RuntimeError("Native release smoke test returned unexpected events.")


def build(build_dir: Path) -> Path:
    cmake = shutil.which("cmake")
    if not cmake:
        raise RuntimeError("CMake is required to build the v0.17 native release extension.")
    build_dir = build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    configure = [
        cmake,
        "-S", str(SOURCE),
        "-B", str(build_dir),
        "-DB300_NATIVE_BUILD_TESTS=OFF",
        "-DB300_NATIVE_BUILD_PYTHON=ON",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-Dpybind11_DIR={_pybind_cmake_dir()}",
        f"-DPython_EXECUTABLE={sys.executable}",
    ]
    _run(configure)
    _run([cmake, "--build", str(build_dir), "--config", "Release", "--parallel", "2"])
    candidates = _candidate_extensions(build_dir)
    if len(candidates) != 1:
        raise RuntimeError(
            "Expected exactly one _b300_debug_core extension, found: "
            + ", ".join(str(path) for path in candidates)
        )
    _verify_extension(candidates[0])
    return candidates[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build/native-release")
    parser.add_argument(
        "--github-env",
        action="store_true",
        help="Append B300_NATIVE_EXTENSION to the file named by GITHUB_ENV.",
    )
    args = parser.parse_args(argv)
    extension = build(Path(args.build_dir))
    print(str(extension))
    if args.github_env:
        env_file = os.environ.get("GITHUB_ENV", "").strip()
        if not env_file:
            raise RuntimeError("--github-env requires GITHUB_ENV to be set.")
        with Path(env_file).open("a", encoding="utf-8") as handle:
            handle.write(f"B300_NATIVE_EXTENSION={extension}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
