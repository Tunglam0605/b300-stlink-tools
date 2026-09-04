"""MCU target metadata kept independent from Qt and transport layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class MemoryRegion:
    name: str
    start: int
    length: int
    kind: str
    writable: bool

    @property
    def end(self) -> int:
        return self.start + self.length

    def contains(self, address: int, length: int = 1) -> bool:
        address = int(address)
        length = int(length)
        return length > 0 and self.start <= address and address + length <= self.end


@dataclass(frozen=True)
class TargetCapabilities:
    dwt: bool = False
    fpu: bool = False
    nvic: bool = True
    scb: bool = True
    svd: bool = False
    swo: bool = False
    itm: bool = False


@dataclass(frozen=True)
class TargetDescription:
    key: str
    vendor: str
    family: str
    part: str
    core: str
    flash_bytes: int
    breakpoint_count: int
    watchpoint_count: int
    memory_regions: Tuple[MemoryRegion, ...]
    capabilities: TargetCapabilities
    svd_hint: Optional[str] = None

    def classify_address(self, address: int, length: int = 1) -> Optional[MemoryRegion]:
        for region in self.memory_regions:
            if region.contains(address, length):
                return region
        return None

    def require_region(self, address: int, length: int = 1) -> MemoryRegion:
        region = self.classify_address(address, length)
        if region is None:
            raise ValueError("Address range 0x%08X..0x%08X is outside known target regions." % (
                int(address), int(address) + int(length) - 1,
            ))
        return region
