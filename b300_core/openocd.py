"""Safe OpenOCD command generation, execution and boot-state parsing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .models import BootVerification, CommandResult, FlashPlan, ProbeRef
from .policy import APPLICATION_ADDRESS, FLASH_END_ADDRESS, STLINK_PROVISION_MAGIC


EventSink = Callable[[str], None]


def resolve_openocd(explicit: Optional[str] = None) -> str:
    return explicit or os.environ.get("B300_OPENOCD") or shutil.which("openocd") or "openocd"


def validate_openocd_value(value: object, label: str) -> None:
    if any(character in str(value) for character in "{}\r\n"):
        raise ValueError("%s contains an unsafe character for OpenOCD." % label)


def _base_command(probe: ProbeRef, executable: str, *, gdb_port: Optional[int] = None,
                  telnet_port: Optional[int] = None, bind_address: str = "127.0.0.1") -> List[str]:
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
        "-c", "tcl port disabled",
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
        "-c", "mww 0x40023840 0x10000000",
        "-c", "mww 0x40007000 0x00000100",
        "-c", "mww 0x40002860 0x%08X" % STLINK_PROVISION_MAGIC,
        "-c", "reset run",
        "-c", "shutdown",
    ]


def build_boot_verify_command(probe: ProbeRef, executable: str) -> List[str]:
    return _base_command(probe, executable) + [
        "-c", "init",
        "-c", "reset run",
        "-c", "sleep 1000",
        "-c", "halt",
        "-c", "reg pc",
        "-c", "mdw 0x40002854 4",
        "-c", "resume",
        "-c", "shutdown",
    ]


def build_debug_command(probe: ProbeRef, executable: str, bind_address: str,
                        gdb_port: int, telnet_port: Optional[int] = None) -> List[str]:
    return _base_command(
        probe,
        executable,
        gdb_port=gdb_port,
        telnet_port=telnet_port,
        bind_address=bind_address,
    ) + ["-c", "init"]


def parse_boot_verification(output: str) -> BootVerification:
    pc_match = re.search(r"pc\s+\(/32\):\s+0x([0-9A-Fa-f]+)", output)
    bkp_match = re.search(
        r"0x40002854:\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s+"
        r"([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})",
        output,
    )
    pc = int(pc_match.group(1), 16) if pc_match else None
    bkp1r = int(bkp_match.group(1), 16) if bkp_match else None
    bkp4r = int(bkp_match.group(4), 16) if bkp_match else None

    if pc is None:
        return BootVerification(pc, bkp1r, bkp4r, False, "OpenOCD did not report PC.")
    if not APPLICATION_ADDRESS <= pc < FLASH_END_ADDRESS:
        return BootVerification(pc, bkp1r, bkp4r, False,
                                "CPU remains in Bootloader or outside Application.")
    if bkp1r is None or bkp4r is None:
        return BootVerification(pc, bkp1r, bkp4r, False,
                                "OpenOCD did not report backup registers.")
    if bkp1r != 0 or bkp4r != 0:
        return BootVerification(pc, bkp1r, bkp4r, False,
                                "Bootloader did not clear retained provisioning state.")
    return BootVerification(pc, bkp1r, bkp4r, True, "Application is running.")


class OpenOcdRunner:
    """Execute one command without a shell and stream normalized log lines."""

    def run(self, command: Sequence[str], event_sink: Optional[EventSink] = None) -> CommandResult:
        normalized = tuple(str(item) for item in command)
        try:
            process = subprocess.Popen(
                normalized,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
            )
        except OSError as error:
            return CommandResult(normalized, 127, "OpenOCD not found: %s" % error)

        lines = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            lines.append(line)
            if event_sink is not None:
                event_sink(line)
        process.stdout.close()
        returncode = process.wait()
        return CommandResult(normalized, returncode, "\n".join(lines))
