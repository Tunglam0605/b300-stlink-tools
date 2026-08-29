"""Safe OpenOCD command generation, execution and boot-state parsing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .models import BootVerification, CommandResult, FactoryPlan, FlashPlan, ProbeRef, TargetInfo
from .offline_setup import installed_openocd_path, verify_openocd_tree
from .policy import APPLICATION_ADDRESS, FLASH_END_ADDRESS
from .process_startup import child_process_kwargs


EventSink = Callable[[str], None]


def _packaged_openocd_candidates() -> List[Path]:
    name = "openocd.exe" if os.name == "nt" else "openocd"
    roots = []

    configured_root = os.environ.get("B300_APP_ROOT")
    if configured_root:
        roots.append(Path(configured_root))

    # PyInstaller one-file executables report the launched executable through
    # sys.executable. The portable archive and DEB both place vendor/ beside it.
    roots.append(Path(sys.executable).resolve().parent)

    # AppImage exposes its extracted root through APPDIR.
    appdir = os.environ.get("APPDIR")
    if appdir:
        roots.append(Path(appdir) / "usr" / "lib" / "b300-stlink")

    # Canonical DEB install root. This also makes recovery deterministic if the
    # desktop launcher environment is stripped by the session manager.
    if os.name != "nt":
        roots.append(Path("/opt/b300-stlink"))

    candidates = []
    seen = set()
    for root in roots:
        candidate = root / "vendor" / "openocd" / "bin" / name
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


def _usable_verified_openocd(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name != "nt" and not os.access(path, os.X_OK):
        return False
    return verify_openocd_tree(path.parent.parent)


def resolve_openocd(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    configured = os.environ.get("B300_OPENOCD")
    if configured and (Path(configured).is_file() or shutil.which(configured)):
        return configured
    for bundled in _packaged_openocd_candidates():
        if _usable_verified_openocd(bundled):
            return str(bundled)
    installed = installed_openocd_path()
    if _usable_verified_openocd(installed):
        return str(installed)
    return shutil.which("openocd") or "openocd"


def validate_openocd_value(value: object, label: str) -> None:
    if any(character in str(value) for character in "{}\r\n"):
        raise ValueError("%s contains an unsafe character for OpenOCD." % label)


def _base_command(probe: ProbeRef, executable: str, *, gdb_port: Optional[int] = None,
                  telnet_port: Optional[int] = None, tcl_port: Optional[int] = None,
                  bind_address: str = "127.0.0.1") -> List[str]:
    validate_openocd_value(executable, "OpenOCD path")
    validate_openocd_value(bind_address, "Bind address")
    command = [
        executable,
        "-c", "bindto %s" % bind_address,
        "-f", "interface/stlink.cfg",
        "-c", "transport select swd",
        "-f", "target/stm32f4x.cfg",
        "-c", "gdb port %s" % (gdb_port if gdb_port is not None else "disabled"),
        "-c", "telnet port %s" % (telnet_port if telnet_port is not None else "disabled"),
        "-c", "tcl port %s" % (tcl_port if tcl_port is not None else "disabled"),
    ]
    if probe.serial:
        validate_openocd_value(probe.serial, "Probe serial")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", probe.serial):
            raise ValueError("Probe serial contains unsupported characters.")
        command.extend(["-c", "adapter serial %s" % probe.serial])
    return command


def build_flash_command(plan: FlashPlan, executable: str) -> List[str]:
    validate_openocd_value(plan.image.path, "Application path")
    if plan.erase_sectors != (3, 4, 5, 6, 7):
        raise ValueError("Unsafe flash plan: erase sectors must be exactly 3..7.")
    return _base_command(plan.probe, executable) + [
        "-c", "init",
        "-c", "reset init",
        "-c", "flash erase_sector 0 3 7",
        "-c", "program {%s} verify" % plan.image.path,
        "-c", "shutdown",
    ]


def build_factory_protect_command(probe: ProbeRef, executable: str, enabled: bool) -> List[str]:
    """Toggle write protection only for Bootloader sectors 0..2."""
    state = "on" if enabled else "off"
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "reset init",
        "-c", "flash protect 0 0 2 %s" % state,
        # STM32F4 option-byte changes take effect only after a reset/reload.
        # Halt immediately after reset so Application/Bootloader code cannot run
        # while the factory transaction is between protection states.
        "-c", "reset halt",
        "-c", "shutdown",
    ]


def build_factory_flash_command(plan: FactoryPlan, executable: str) -> List[str]:
    validate_openocd_value(plan.image.path, "Bootloader path")
    if plan.erase_sectors != (0, 1, 2):
        raise ValueError("Unsafe factory plan: erase sectors must be exactly 0..2.")
    return _base_command(plan.probe, executable) + [
        "-c", "init",
        "-c", "reset init",
        "-c", "flash erase_sector 0 0 2",
        "-c", "program {%s} verify" % plan.image.path,
        "-c", "shutdown",
    ]


def build_reset_command(probe: ProbeRef, executable: str) -> List[str]:
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "reset run",
        "-c", "shutdown",
    ]


def build_resume_command(probe: ProbeRef, executable: str) -> List[str]:
    """Best-effort recovery for an interrupted read-only halt session."""
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "resume",
        "-c", "shutdown",
    ]


def program_verify_succeeded(output: str) -> bool:
    """Accept only OpenOCD's complete verified-success event line."""
    return any(line.strip() == "** Verified OK **" for line in output.splitlines())


def build_boot_verify_command(probe: ProbeRef, executable: str) -> List[str]:
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "sleep 1000",
        "-c", "halt",
        "-c", "reg pc",
        "-c", "mdw 0x40002854 1",
        "-c", "resume",
        "-c", "shutdown",
    ]


def build_debug_command(probe: ProbeRef, executable: str, bind_address: str,
                        gdb_port: int, telnet_port: Optional[int] = None,
                        tcl_port: Optional[int] = None) -> List[str]:
    return _base_command(
        probe,
        executable,
        gdb_port=gdb_port,
        telnet_port=telnet_port,
        tcl_port=tcl_port,
        bind_address=bind_address,
    ) + [
        "-c", "gdb flash_program disable",
        "-c", "gdb breakpoint_override hard",
        "-c", "init",
    ]


def build_target_inspect_command(probe: ProbeRef, executable: str) -> List[str]:
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "flash info 0",
        "-c", "shutdown",
    ]


def parse_target_info(output: str) -> TargetInfo:
    voltage_match = re.search(r"Target voltage:\s*([0-9]+(?:\.[0-9]+)?)", output,
                              re.IGNORECASE)
    device_match = re.search(r"device id\s*=\s*0x([0-9A-Fa-f]+)", output,
                             re.IGNORECASE)
    flash_match = re.search(r"flash size\s*=\s*([0-9]+)\s*KiB", output,
                            re.IGNORECASE)
    protection_lines = []
    sector_states = []
    for line in output.splitlines():
        if "protect" in line.lower():
            protection_lines.append(line.strip())
        sector_match = re.search(
            r"#\s*(\d+):.*?\b(not protected|protected)\s*$",
            line,
            re.IGNORECASE,
        )
        if sector_match:
            sector_states.append((
                int(sector_match.group(1)),
                sector_match.group(2).lower() == "protected",
            ))
    if not voltage_match or not device_match or not flash_match:
        raise ValueError("OpenOCD target inspection did not identify voltage/device/flash size.")
    protection_summary = " | ".join(protection_lines) or "Protection status not reported"
    sector_states.sort(key=lambda item: item[0])
    if sector_states:
        groups = []
        start, end, protected = sector_states[0][0], sector_states[0][0], sector_states[0][1]
        for sector, state in sector_states[1:]:
            if sector == end + 1 and state == protected:
                end = sector
                continue
            groups.append((start, end, protected))
            start, end, protected = sector, sector, state
        groups.append((start, end, protected))
        protection_summary = "; ".join(
            "Sector %s %s" % (
                str(first) if first == last else "%d–%d" % (first, last),
                "protected" if state else "not protected",
            )
            for first, last, state in groups
        )
    protected_sectors = tuple(sector for sector, state in sector_states if state)
    protection_reported = (
        tuple(sector for sector, _state in sector_states) == tuple(range(8))
    )
    return TargetInfo(
        device_id=int(device_match.group(1), 16),
        flash_kib=int(flash_match.group(1)),
        target_voltage=float(voltage_match.group(1)),
        protection_summary=protection_summary,
        protected_sectors=protected_sectors,
        protection_reported=protection_reported,
        readout_protected=("device security bit set" in output.lower()),
    )


def parse_boot_verification(output: str) -> BootVerification:
    pc_match = re.search(r"pc\s+\(/32\):\s+0x([0-9A-Fa-f]+)", output)
    bkp_match = re.search(r"0x40002854:\s+([0-9A-Fa-f]{8})", output)
    pc = int(pc_match.group(1), 16) if pc_match else None
    bkp1r = int(bkp_match.group(1), 16) if bkp_match else None

    if pc is None:
        return BootVerification(pc, bkp1r, False, "OpenOCD did not report PC.")
    if not APPLICATION_ADDRESS <= pc < FLASH_END_ADDRESS:
        return BootVerification(pc, bkp1r, False,
                                "CPU remains in Bootloader or outside Application.")
    if bkp1r is None:
        return BootVerification(pc, bkp1r, False,
                                "OpenOCD did not report BKP1R recovery state.")
    if bkp1r != 0:
        return BootVerification(pc, bkp1r, False,
                                "Bootloader did not clear the BKP1R recovery state.")
    return BootVerification(pc, bkp1r, True, "Application is running.")


class OpenOcdRunner:
    """Execute one command without a shell and stream normalized log lines."""

    def __init__(self, process_factory: Optional[Callable[..., subprocess.Popen]] = None,
                 platform_name: Optional[str] = None) -> None:
        self._process_factory = process_factory or subprocess.Popen
        self._platform_name = platform_name

    def run(self, command: Sequence[str], event_sink: Optional[EventSink] = None,
            timeout_seconds: Optional[float] = 60.0,
            cancel_event: Optional[threading.Event] = None) -> CommandResult:
        normalized = tuple(str(item) for item in command)
        try:
            process = self._process_factory(
                normalized,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                **child_process_kwargs(self._platform_name),
            )
        except OSError as error:
            return CommandResult(normalized, 127, "OpenOCD not found: %s" % error)

        lines = []
        assert process.stdout is not None
        reader_done = threading.Event()

        def read_output() -> None:
            try:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    lines.append(line)
                    if event_sink is not None:
                        event_sink(line)
            finally:
                process.stdout.close()
                reader_done.set()

        reader = threading.Thread(
            target=read_output,
            name="b300-openocd-output",
            daemon=True,
        )
        reader.start()
        deadline = (time.monotonic() + timeout_seconds
                    if timeout_seconds is not None else None)
        timed_out = False
        cancelled = False

        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            reader_done.wait(0.02)

        if timed_out or cancelled:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

        returncode = process.wait()
        reader.join(timeout=2.0)
        if timed_out:
            lines.append("OpenOCD operation timed out after %.1f seconds." % timeout_seconds)
        elif cancelled:
            lines.append("OpenOCD operation was cancelled.")
        return CommandResult(
            normalized,
            returncode,
            "\n".join(lines),
            timed_out=timed_out,
            cancelled=cancelled,
        )
