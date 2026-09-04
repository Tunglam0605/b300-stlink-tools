"""HALT-only, read-only peripheral inspection backed by DebugMemoryBackend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from b300_core.debug_memory import DebugMemoryBackend
from b300_core.target.target_description import TargetDescription
from .svd_model import SvdDevice, SvdPeripheral, SvdRegister


@dataclass(frozen=True)
class PeripheralFieldValue:
    name: str
    value: int
    bit_offset: int
    bit_width: int


@dataclass(frozen=True)
class PeripheralRegisterSnapshot:
    peripheral: str
    register: str
    address: int
    size_bits: int
    raw_value: int
    fields: Tuple[PeripheralFieldValue, ...]


class PeripheralService:
    """Inspect one selected SVD register without polling the whole MCU."""

    def __init__(self, memory: DebugMemoryBackend, target: TargetDescription,
                 device: Optional[SvdDevice] = None) -> None:
        self.memory = memory
        self.target = target
        self.device = device

    def set_device(self, device: SvdDevice) -> None:
        self.device = device

    def _require_device(self) -> SvdDevice:
        if self.device is None:
            raise RuntimeError("No SVD is loaded for Peripheral Inspector.")
        return self.device

    def peripherals(self) -> Tuple[SvdPeripheral, ...]:
        return self._require_device().peripherals

    def registers(self, peripheral: str) -> Tuple[SvdRegister, ...]:
        return self._require_device().peripheral(peripheral).registers

    def inspect_register(self, peripheral: str, register: str) -> PeripheralRegisterSnapshot:
        device = self._require_device()
        p = device.peripheral(peripheral)
        r = p.register(register)
        if r.size_bits not in (8, 16, 32):
            raise ValueError("Peripheral Inspector currently supports 8/16/32-bit registers only.")
        address = p.base_address + r.address_offset
        region = self.target.classify_address(address, r.size_bits // 8)
        if region is None or region.kind not in {"peripheral", "system"}:
            raise ValueError("SVD register address 0x%08X is outside peripheral/system regions." % address)
        block = self.memory.read(address, r.size_bits // 8)
        raw = int.from_bytes(block.data, byteorder="little", signed=False)
        fields = tuple(
            PeripheralFieldValue(field.name, field.extract(raw), field.bit_offset, field.bit_width)
            for field in r.fields
        )
        return PeripheralRegisterSnapshot(
            peripheral=p.name,
            register=r.name,
            address=address,
            size_bits=r.size_bits,
            raw_value=raw,
            fields=fields,
        )
