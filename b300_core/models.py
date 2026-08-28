"""Immutable data exchanged by the CLI, GUI and hardware services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class ProbeRef:
    """A selected ST-Link; ``None`` lets OpenOCD auto-select one probe."""

    serial: Optional[str] = None


@dataclass(frozen=True)
class ProbeInfo:
    serial: Optional[str]
    name: str
    source: str
    usb_identity: Optional[str] = None
    status: str = "available"

    @property
    def serial_available(self) -> bool:
        """Whether this physical probe can be explicitly pinned in OpenOCD."""
        return self.serial is not None


@dataclass(frozen=True)
class SectorInfo:
    index: int
    start_address: int
    end_address: int
    role: str
    writable: bool

    @property
    def size(self) -> int:
        return self.end_address - self.start_address + 1


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    sha256: str
    start_address: int
    end_address: int
    size: int
    data_record_count: int


@dataclass(frozen=True)
class FlashPlan:
    image: ImageInfo
    probe: ProbeRef
    erase_sectors: Tuple[int, ...]
    target: TargetInfo


@dataclass(frozen=True)
class FlashPreview:
    image: ImageInfo
    probe: ProbeRef
    erase_sectors: Tuple[int, ...]


@dataclass(frozen=True)
class FactoryPlan:
    image: ImageInfo
    probe: ProbeRef
    erase_sectors: Tuple[int, ...]
    target: TargetInfo


@dataclass(frozen=True)
class FactoryPreview:
    image: ImageInfo
    probe: ProbeRef
    erase_sectors: Tuple[int, ...]


@dataclass(frozen=True)
class CommandResult:
    command: Tuple[str, ...]
    returncode: int
    output: str
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class BootVerification:
    pc: Optional[int]
    bkp1r: Optional[int]
    passed: bool
    reason: str


@dataclass(frozen=True)
class OtaMetadata:
    classification: str
    valid: bool
    magic: int
    format_version: int
    state: int
    state_name: str
    image_size: int
    image_crc32: int
    board_token: str
    sequence: int
    meta_crc32: int
    calculated_meta_crc32: int


@dataclass(frozen=True)
class TargetInfo:
    device_id: int
    flash_kib: int
    target_voltage: float
    protection_summary: str
    protected_sectors: Tuple[int, ...] = ()
    protection_reported: bool = False
    readout_protected: bool = False


@dataclass(frozen=True)
class FlashPhaseEvent:
    phase: str
    progress: int
    message: str
    cancellable: bool = False
