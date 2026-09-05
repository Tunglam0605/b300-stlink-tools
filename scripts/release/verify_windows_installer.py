"""Exercise the exact candidate installer in a new, isolated test directory.

No hardware access. Logs and a JSON verdict remain in --evidence-dir. A timeout
is always a failure; kill the installer process tree before returning it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from b300_core.runtime_integrity import validate_runtime, write_runtime_manifest
from b300_version import __version__


def run(command: list[str], timeout: int = 180, *, expect_failure: bool = False) -> None:
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       check=False, capture_output=True, timeout=20)
        process.wait(timeout=20)
        raise RuntimeError("Timed out (not PASS): " + command[0])
    if (code == 0) == expect_failure:
        raise RuntimeError("Unexpected exit code %d: %r" % (code, command))


def hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def verify(installer: Path, evidence: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows installer verification requires Windows")
    installer = installer.resolve(strict=True)
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    installed = evidence / "installed"
    common = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
              "/SP-", "/NOICONS", "/NOCLOSEAPPLICATIONS", "/B300TESTISOLATED=1",
              "/DIR=" + str(installed)]
    verdict = {"installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest()}

    def setup(phase: str, *args: str, fail: bool = False) -> None:
        run([*common, "/LOG=" + str(evidence / (phase + ".log")), *args], expect_failure=fail)

    def smoke() -> None:
        validate_runtime(installed, __version__)
        run([str(installed / "b300-stlink-gui.exe"), "--smoke-test"], timeout=60)

    try:
        setup("fresh")
        smoke()
        run([str(installed / "vendor/openocd/bin/openocd.exe"), "--version"], timeout=30)
        run([str(installed / "vendor/gdb/bin/arm-none-eabi-gdb.exe"), "--version"], timeout=30)
        verdict["fresh_install_and_tools"] = "PASS"
        stale = installed / "_internal/stale-runtime.pyc"
        stale.write_bytes(b"obsolete-runtime-from-old-fixture")
        user_file = installed / "user-preservation-fixture.txt"
        user_file.write_bytes(b"user-owned-data")
        # Change publisher metadata to ensure rollback restores old bytes,
        # rather than merely reproducing an identical candidate tree.
        metadata = installed / "BUNDLE-METADATA.txt"
        metadata.write_bytes(metadata.read_bytes() + b"\nold-fixture=1\n")
        write_runtime_manifest(installed, __version__)
        before = hashes(installed)
        (evidence / "before-tree-hashes.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
        for phase, hook in (("early-rollback", "1"), ("late-rollback", "late")):
            setup(phase, "/B300TESTFAILUPGRADE=" + hook, fail=True)
            after = hashes(installed)
            (evidence / (phase + "-hashes.json")).write_text(json.dumps(after, indent=2), encoding="utf-8")
            if after != before:
                raise RuntimeError("Failed upgrade changed the previous tree: " + phase)
            smoke()
            verdict[phase] = "PASS"
        setup("upgrade")
        if stale.exists():
            raise RuntimeError("Stale runtime survived installer upgrade")
        if user_file.read_bytes() != b"user-owned-data":
            raise RuntimeError("Upgrade changed user-owned data")
        if b"old-fixture=1" in metadata.read_bytes():
            raise RuntimeError("Upgrade retained old publisher metadata")
        smoke()
        verdict["successful_upgrade_and_stale_cleanup"] = "PASS"
        # Only delete the installation we created below this new evidence root.
        if installed.resolve().parent != evidence:
            raise RuntimeError("Unsafe cleanup path")
        shutil.rmtree(installed)
        verdict["cleanup"] = "PASS"
    except Exception as error:
        verdict["failure"] = str(error)
        raise
    finally:
        (evidence / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        print(json.dumps(verdict, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    verify(args.installer, args.evidence_dir)
