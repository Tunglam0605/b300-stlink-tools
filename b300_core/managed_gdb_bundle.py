"""Build a compact, B300-managed GNU Arm GDB runtime from a trusted toolchain.

The release builder downloads and verifies the pinned xPack GNU Arm Embedded GCC
archive because that upstream package provides a portable ``arm-none-eabi-gdb``
for every B300 release architecture. B300 does not need to ship a compiler,
linker, C library, or the target runtime just to attach Cortex-Debug to an
external OpenOCD server, so this module stages only the host-side files needed
by GDB and the nm/addr2line/objdump tools used by Monitor and Cortex-Debug,
plus upstream notices.

This module is build-time only. It never downloads software and never touches
the target MCU.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


MANAGED_GDB_VERSION = "15.2.1-1.1"
MANAGED_GDB_UPSTREAM = (
    "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/"
    "releases/tag/v15.2.1-1.1"
)
NOTICE_NAME = "B300-MANAGED-GDB.txt"
SYMBOL_TOOLS = ("nm", "addr2line", "objdump")


def _gdb_name(platform_name: str) -> str:
    if platform_name == "windows-x64":
        return "arm-none-eabi-gdb.exe"
    if platform_name in {"linux-x64", "linux-arm64"}:
        return "arm-none-eabi-gdb"
    raise RuntimeError("Unsupported managed GDB platform: %s" % platform_name)


def _copy_file(source: Path, source_root: Path, destination_root: Path) -> None:
    relative = source.relative_to(source_root)
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree_files(source: Path, source_root: Path, destination_root: Path) -> None:
    if not source.is_dir():
        return
    for candidate in sorted(source.rglob("*")):
        if candidate.is_file():
            _copy_file(candidate, source_root, destination_root)


def _unique_files(candidates: Iterable[Path]) -> tuple[Path, ...]:
    seen = set()
    output = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        output.append(candidate)
    return tuple(output)


def _host_runtime_files(source_root: Path, platform_name: str) -> tuple[Path, ...]:
    """Return host-side shared libraries required by the portable GDB.

    The pinned xPack 15.2.1-1.1 layout stores Windows DLLs under ``bin``.
    On GNU/Linux the debugger dependencies are primarily top-level shared
    objects under ``libexec`` (for example ``libiconv.so.2``), with a small
    number of host libraries under ``lib``/``lib64``. Only top-level shared
    objects are selected; compiler-specific trees such as ``libexec/gcc`` and
    ``lib/gcc`` remain excluded from the B300 debug runtime.
    """
    bin_root = source_root / "bin"
    candidates = []
    if platform_name == "windows-x64":
        candidates.extend(bin_root.glob("*.dll"))
        dll_root = bin_root / "DLLs"
        if dll_root.is_dir():
            candidates.extend(path for path in dll_root.rglob("*") if path.is_file())
    else:
        # Keep the upstream relative layout. xPack GDB is linked to find its
        # portable host libraries relative to the executable, so flattening
        # libexec into bin would make the release less reproducible.
        candidates.extend(bin_root.glob("*.so"))
        candidates.extend(bin_root.glob("*.so.*"))
        for library_root in (
                source_root / "lib",
                source_root / "lib64",
                source_root / "libexec",
        ):
            if library_root.is_dir():
                candidates.extend(library_root.glob("*.so"))
                candidates.extend(library_root.glob("*.so.*"))
    return _unique_files(candidates)


def stage_managed_gdb_runtime(
        source_root: Path, destination_root: Path, platform_name: str) -> Path:
    """Stage the smallest supported B300 GDB runtime from an extracted xPack.

    The destination is fail-closed: it must not contain pre-existing files.
    The compiler and linker are deliberately excluded so installing B300 does
    not silently become an application build toolchain installation.
    """
    source = Path(source_root).resolve()
    destination = Path(destination_root)
    if not source.is_dir():
        raise ValueError("Extracted GDB source root does not exist.")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Managed GDB destination must be empty.")
    destination.mkdir(parents=True, exist_ok=True)

    executable = source / "bin" / _gdb_name(platform_name)
    if not executable.is_file():
        raise ValueError("Extracted toolchain does not contain arm-none-eabi-gdb.")
    tools = [source / "bin" / ("arm-none-eabi-" + tool + executable.suffix)
             for tool in SYMBOL_TOOLS]
    for tool in tools:
        if not tool.is_file():
            raise ValueError("Extracted toolchain does not contain %s." % tool.name)
    for tool in [executable] + tools:
        _copy_file(tool, source, destination)
        if platform_name != "windows-x64":
            staged_tool = destination / "bin" / tool.name
            staged_tool.chmod(staged_tool.stat().st_mode | 0o111)

    for runtime_file in _host_runtime_files(source, platform_name):
        _copy_file(runtime_file, source, destination)

    # Preserve upstream documentation and the complete license inventory. The
    # public release gate must still perform the project's normal third-party
    # redistribution review; this staging step does not make a legal judgment.
    for root_name in ("README.md", "LICENSE", "COPYING", "COPYING3"):
        candidate = source / root_name
        if candidate.is_file():
            _copy_file(candidate, source, destination)
    _copy_tree_files(source / "distro-info", source, destination)

    # GDB data files are optional in some xPack layouts but required when
    # present. Copy only the GDB-specific subtree, not GCC documentation.
    _copy_tree_files(source / "share" / "gdb", source, destination)

    (destination / NOTICE_NAME).write_text(
        "B300 managed GNU Arm GDB runtime\n"
        "version=%s\n"
        "upstream=%s\n"
        "scope=debugger-and-symbol-tools; compiler/linker intentionally excluded\n"
        "licenses=distro-info/licenses (when supplied by upstream)\n"
        % (MANAGED_GDB_VERSION, MANAGED_GDB_UPSTREAM),
        encoding="utf-8",
    )

    staged = destination / "bin" / _gdb_name(platform_name)
    if platform_name != "windows-x64":
        staged.chmod(staged.stat().st_mode | 0o111)

    compiler_name = (
        "arm-none-eabi-gcc.exe"
        if platform_name == "windows-x64"
        else "arm-none-eabi-gcc"
    )
    if (destination / "bin" / compiler_name).exists():
        raise AssertionError("Managed GDB runtime must not contain the GCC compiler.")
    return staged


def smoke_test_managed_gdb(executable: Path) -> str:
    """Prove the staged GDB can actually start before a release is packaged.

    No B300-only ``LD_LIBRARY_PATH`` is injected here. The staged runtime must
    remain self-contained in the same relative layout expected by upstream so
    the exact executable path handed to Cortex-Debug also works on a clean
    client machine.
    """
    binary = Path(executable)
    if not binary.is_file():
        raise RuntimeError("Managed GDB smoke test executable is missing.")
    env = os.environ.copy()
    env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [str(binary), "--nx", "--batch", "-ex", "show architecture"],
        capture_output=True,
        text=True,
        timeout=20.0,
        shell=False,
        env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise RuntimeError(
            "Managed GDB failed its startup smoke test (exit %d): %s"
            % (completed.returncode, output.strip())
        )
    if "architecture" not in output.lower():
        raise RuntimeError("Managed GDB smoke test produced no architecture response.")
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=20.0,
        shell=False,
        env=env,
    )
    if version.returncode != 0:
        raise RuntimeError("Managed GDB version probe failed after startup smoke test.")
    for stem in SYMBOL_TOOLS:
        tool = binary.with_name("arm-none-eabi-" + stem + binary.suffix)
        if not tool.is_file():
            raise RuntimeError("Managed symbol tool is missing: %s" % tool.name)
        probe = subprocess.run(
            [str(tool), "--version"], capture_output=True, text=True,
            timeout=20.0, shell=False, env=env,
        )
        if probe.returncode != 0:
            raise RuntimeError("Managed symbol tool smoke test failed: %s: %s"
                               % (tool.name, (probe.stderr or probe.stdout).strip()))
    first_line = (version.stdout or version.stderr or "").splitlines()
    return first_line[0].strip() if first_line else "GNU GDB"
