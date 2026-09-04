"""Fail-closed remote programming control plane for B300 Gateways.

The transport may carry HEX/BIN/ELF/AXF files, but the Gateway never exposes raw
memory/erase commands. Every executable Application transaction is converted into
an existing B300Service safety plan before ST-Link is touched.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Tuple

from .models import FlashPhaseEvent, FlashPlan, ProbeRef
from .service import B300Service, FlashResult


class RemoteProgrammingDenied(RuntimeError):
    """A remote request is validly formed but forbidden by B300 policy."""


class FirmwareKind(str, Enum):
    APPLICATION = "APPLICATION"
    BOOTLOADER = "BOOTLOADER"


class RemoteProgrammingOperation(str, Enum):
    READ_TARGET = "READ_TARGET"
    FLASH_APPLICATION = "FLASH_APPLICATION"
    FLASH_BOOTLOADER = "FLASH_BOOTLOADER"
    VERIFY = "VERIFY"


class RemotePrivilege(str, Enum):
    STANDARD = "STANDARD"
    ELEVATED = "ELEVATED"


_ALLOWED_SUFFIXES = {".hex", ".bin", ".elf", ".axf"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_FIRMWARE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class RemoteFirmwareManifest:
    """Content-addressed firmware declaration sent by a Client to a Gateway."""

    operation: RemoteProgrammingOperation
    firmware_kind: FirmwareKind
    file_name: str
    size: int
    sha256: str
    target: str = "STM32F407ZET6"
    board: str = "B300"
    privilege: RemotePrivilege = RemotePrivilege.STANDARD

    def validate(self) -> "RemoteFirmwareManifest":
        operation = RemoteProgrammingOperation(self.operation)
        kind = FirmwareKind(self.firmware_kind)
        privilege = RemotePrivilege(self.privilege)
        name = str(self.file_name).strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Remote firmware file name must be a plain basename.")
        if Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
            raise ValueError("Remote firmware type must be HEX, BIN, ELF or AXF.")
        if not 0 < int(self.size) <= _MAX_FIRMWARE_BYTES:
            raise ValueError("Remote firmware size is outside the allowed range.")
        digest = str(self.sha256).strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("Remote firmware SHA-256 is invalid.")
        if str(self.target).upper() not in {"STM32F407ZET6", "STM32F407ZE"}:
            raise ValueError("Remote firmware target is not the B300 STM32F407ZE family.")
        if str(self.board).upper() != "B300":
            raise ValueError("Remote firmware board must be B300.")
        if operation == RemoteProgrammingOperation.FLASH_APPLICATION:
            if kind != FirmwareKind.APPLICATION:
                raise ValueError("FLASH_APPLICATION requires Application firmware.")
            if privilege != RemotePrivilege.STANDARD:
                raise ValueError("Application flashing uses the standard privilege class.")
        elif operation == RemoteProgrammingOperation.FLASH_BOOTLOADER:
            if kind != FirmwareKind.BOOTLOADER:
                raise ValueError("FLASH_BOOTLOADER requires Bootloader firmware.")
            if privilege != RemotePrivilege.ELEVATED:
                raise ValueError("Remote Bootloader flashing requires elevated privilege.")
        return self

    @classmethod
    def from_file(cls, path: Path, *, operation: RemoteProgrammingOperation,
                  firmware_kind: FirmwareKind,
                  privilege: RemotePrivilege = RemotePrivilege.STANDARD,
                  target: str = "STM32F407ZET6", board: str = "B300") -> "RemoteFirmwareManifest":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Firmware file does not exist.")
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > _MAX_FIRMWARE_BYTES:
                    raise ValueError("Firmware file exceeds the B300 remote transfer size limit.")
                digest.update(chunk)
        return cls(
            operation=RemoteProgrammingOperation(operation),
            firmware_kind=FirmwareKind(firmware_kind),
            file_name=source.name,
            size=size,
            sha256=digest.hexdigest(),
            target=target,
            board=board,
            privilege=RemotePrivilege(privilege),
        ).validate()

    def matches_file(self, path: Path) -> bool:
        self.validate()
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.name != self.file_name:
            return False
        if source.stat().st_size != self.size:
            return False
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == self.sha256.lower()


@dataclass(frozen=True)
class RemoteApplicationApproval:
    """Gateway-side approval produced only after local B300 safety validation."""

    manifest: RemoteFirmwareManifest
    staged_path: Path
    plan: FlashPlan


class GatewayProgrammingService:
    """High-level remote Application programming through existing B300Service.

    Network/authentication code may stage a received firmware file, then call this
    service. This layer deliberately has no mass-erase/raw-write API.
    """

    def __init__(self, service: Optional[B300Service] = None,
                 *, allow_remote_bootloader: bool = False) -> None:
        self.service = service or B300Service()
        # Remote Bootloader programming stays disabled until a separate privileged
        # authorization/audit workflow is implemented and accepted on hardware.
        self.allow_remote_bootloader = bool(allow_remote_bootloader)

    @staticmethod
    def supported_operations() -> Tuple[RemoteProgrammingOperation, ...]:
        return (
            RemoteProgrammingOperation.READ_TARGET,
            RemoteProgrammingOperation.FLASH_APPLICATION,
            RemoteProgrammingOperation.VERIFY,
        )

    @staticmethod
    def _verify_received_file(manifest: RemoteFirmwareManifest, staged_path: Path) -> Path:
        manifest.validate()
        staged = Path(staged_path).expanduser().resolve()
        if not manifest.matches_file(staged):
            raise RemoteProgrammingDenied(
                "Gateway received firmware does not match the approved name/size/SHA-256 manifest."
            )
        return staged

    @staticmethod
    def _require_current_application_parser(staged: Path) -> None:
        # Existing B300Service currently performs the safety-critical address-range
        # inspection on Intel HEX. BIN/ELF/AXF are allowed by the transfer manifest
        # so the protocol does not need redesign later, but execution remains
        # fail-closed until equivalent parsers are added to B300Service.
        if staged.suffix.lower() != ".hex":
            raise RemoteProgrammingDenied(
                "This B300 build can remotely program Application Intel HEX only; "
                "BIN/ELF/AXF transfer is reserved for a later parser-backed release."
            )

    def prepare_application(self, manifest: RemoteFirmwareManifest, staged_path: Path,
                            probe: ProbeRef, *, event_sink=None) -> RemoteApplicationApproval:
        selected = manifest.validate()
        if selected.operation != RemoteProgrammingOperation.FLASH_APPLICATION:
            raise RemoteProgrammingDenied("Gateway Application preparation requires FLASH_APPLICATION.")
        staged = self._verify_received_file(selected, staged_path)
        self._require_current_application_parser(staged)

        # Reuse the established local path. inspect_image/build_flash_plan enforce
        # Application address boundaries; inspect_target enforces physical target
        # identity before any destructive operation is approved.
        image = self.service.inspect_image(staged)
        target = self.service.inspect_target(probe, event_sink=event_sink)
        plan = self.service.plan(image, probe, target)
        return RemoteApplicationApproval(selected, staged, plan)

    def flash_application(self, approval: RemoteApplicationApproval,
                          *, event_sink=None,
                          phase_sink: Optional[Callable[[FlashPhaseEvent], None]] = None,
                          cancel_event=None) -> FlashResult:
        selected = approval.manifest.validate()
        if selected.operation != RemoteProgrammingOperation.FLASH_APPLICATION:
            raise RemoteProgrammingDenied("Gateway can execute only an approved Application transaction here.")
        self._verify_received_file(selected, approval.staged_path)
        # B300Service.flash stages and re-hashes the image again, re-inspects the
        # target and owns HardwareMode.FLASHING. The remote layer therefore cannot
        # bypass local flash safety by mutating the file after approval.
        return self.service.flash(
            approval.plan,
            event_sink=event_sink,
            phase_sink=phase_sink,
            cancel_event=cancel_event,
        )

    def execute_application(self, manifest: RemoteFirmwareManifest, staged_path: Path,
                            probe: ProbeRef, *, event_sink=None, phase_sink=None,
                            cancel_event=None) -> FlashResult:
        approval = self.prepare_application(
            manifest, staged_path, probe, event_sink=event_sink
        )
        return self.flash_application(
            approval, event_sink=event_sink, phase_sink=phase_sink,
            cancel_event=cancel_event,
        )

    def prepare_bootloader(self, manifest: RemoteFirmwareManifest, staged_path: Path,
                           probe: ProbeRef):
        selected = manifest.validate()
        if selected.operation != RemoteProgrammingOperation.FLASH_BOOTLOADER:
            raise RemoteProgrammingDenied("Bootloader preparation requires FLASH_BOOTLOADER.")
        if not self.allow_remote_bootloader:
            raise RemoteProgrammingDenied(
                "Remote Bootloader programming is disabled. Use local factory provisioning."
            )
        raise RemoteProgrammingDenied(
            "Remote Bootloader authorization is not implemented in this release; fail-closed."
        )


__all__ = [
    "FirmwareKind",
    "GatewayProgrammingService",
    "RemoteApplicationApproval",
    "RemoteFirmwareManifest",
    "RemotePrivilege",
    "RemoteProgrammingDenied",
    "RemoteProgrammingOperation",
]
