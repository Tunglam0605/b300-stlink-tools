"""Pure validation of the B300 STM32F407 Application vector table."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .policy import APPLICATION_ADDRESS, FLASH_END_ADDRESS


# B300's STM32F407 main SRAM is 128 KiB plus separate 64 KiB CCM SRAM.
SRAM_START_ADDRESS = 0x20000000
SRAM_END_ADDRESS = 0x20020000
CCM_SRAM_START_ADDRESS = 0x10000000
CCM_SRAM_END_ADDRESS = 0x10010000


@dataclass(frozen=True)
class ApplicationVector:
    initial_msp: int | None
    reset_vector: int | None
    valid: bool
    reason: str


def _is_stack_pointer(value: int) -> bool:
    return (
        SRAM_START_ADDRESS <= value <= SRAM_END_ADDRESS
        or CCM_SRAM_START_ADDRESS <= value <= CCM_SRAM_END_ADDRESS
    )


def inspect_application_vector(data: bytes) -> ApplicationVector:
    """Parse eight bytes without accessing hardware or changing target state."""
    if len(data) < 8:
        return ApplicationVector(None, None, False, "Application vector requires 8 bytes.")
    initial_msp, reset_vector = struct.unpack("<II", bytes(data[:8]))
    if not _is_stack_pointer(initial_msp):
        return ApplicationVector(initial_msp, reset_vector, False,
                                 "Initial MSP is outside STM32F407 SRAM/CCM SRAM.")
    if not reset_vector & 1:
        return ApplicationVector(initial_msp, reset_vector, False,
                                 "Application reset vector is not a Thumb address.")
    reset_address = reset_vector & ~1
    if not APPLICATION_ADDRESS <= reset_address < FLASH_END_ADDRESS:
        return ApplicationVector(initial_msp, reset_vector, False,
                                 "Application reset vector is outside Application flash.")
    return ApplicationVector(initial_msp, reset_vector, True, "Application vector is valid.")
