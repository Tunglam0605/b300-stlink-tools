"""Transport-neutral CMSIS-SVD models used by the debug inspector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SvdField:
    name: str
    bit_offset: int
    bit_width: int
    description: str = ""

    @property
    def mask(self) -> int:
        return ((1 << self.bit_width) - 1) << self.bit_offset

    def extract(self, value: int) -> int:
        return (int(value) & self.mask) >> self.bit_offset


@dataclass(frozen=True)
class SvdRegister:
    name: str
    address_offset: int
    size_bits: int
    access: Optional[str]
    reset_value: Optional[int]
    fields: Tuple[SvdField, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class SvdPeripheral:
    name: str
    base_address: int
    registers: Tuple[SvdRegister, ...]
    description: str = ""

    def register(self, name: str) -> SvdRegister:
        selected = str(name).strip()
        for register in self.registers:
            if register.name == selected:
                return register
        raise KeyError("Unknown register %s.%s" % (self.name, selected))


@dataclass(frozen=True)
class SvdDevice:
    name: str
    peripherals: Tuple[SvdPeripheral, ...]
    description: str = ""

    def peripheral(self, name: str) -> SvdPeripheral:
        selected = str(name).strip()
        for peripheral in self.peripherals:
            if peripheral.name == selected:
                return peripheral
        raise KeyError("Unknown peripheral: %s" % selected)
