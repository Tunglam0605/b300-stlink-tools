"""Pure Cortex-M fault register decoding and exception-frame helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class FaultFlag:
    group: str
    name: str
    bit: int
    description: str


@dataclass(frozen=True)
class ExceptionFrame:
    r0: int
    r1: int
    r2: int
    r3: int
    r12: int
    lr: int
    pc: int
    xpsr: int
    stack_pointer: int
    used_psp: bool
    extended_fp_frame: bool


@dataclass(frozen=True)
class FaultAnalysis:
    cfsr: int
    hfsr: int
    dfsr: int
    afsr: int
    mmfar: int
    bfar: int
    shcsr: int
    flags: Tuple[FaultFlag, ...]
    fault_address: Optional[int]
    fault_address_source: Optional[str]
    exception_frame: Optional[ExceptionFrame]


class CortexMFaultDecoder:
    """Decode ARMv7-M SCB fault status without changing target state."""

    _CFSR_FLAGS = (
        (0, "MemoryManage", "IACCVIOL", "Instruction access violation"),
        (1, "MemoryManage", "DACCVIOL", "Data access violation"),
        (3, "MemoryManage", "MUNSTKERR", "Fault on exception return unstack"),
        (4, "MemoryManage", "MSTKERR", "Fault on exception entry stack"),
        (5, "MemoryManage", "MLSPERR", "Lazy FP state preservation fault"),
        (7, "MemoryManage", "MMARVALID", "MMFAR contains a valid fault address"),
        (8, "BusFault", "IBUSERR", "Instruction bus error"),
        (9, "BusFault", "PRECISERR", "Precise data bus error"),
        (10, "BusFault", "IMPRECISERR", "Imprecise data bus error"),
        (11, "BusFault", "UNSTKERR", "Bus fault on exception return unstack"),
        (12, "BusFault", "STKERR", "Bus fault on exception entry stack"),
        (13, "BusFault", "LSPERR", "Lazy FP state preservation bus fault"),
        (15, "BusFault", "BFARVALID", "BFAR contains a valid fault address"),
        (16, "UsageFault", "UNDEFINSTR", "Undefined instruction"),
        (17, "UsageFault", "INVSTATE", "Invalid EPSR state"),
        (18, "UsageFault", "INVPC", "Invalid EXC_RETURN / exception return PC"),
        (19, "UsageFault", "NOCP", "Coprocessor access fault"),
        (24, "UsageFault", "UNALIGNED", "Unaligned access trap"),
        (25, "UsageFault", "DIVBYZERO", "Divide-by-zero trap"),
    )
    _HFSR_FLAGS = (
        (1, "HardFault", "VECTTBL", "Bus fault while reading exception vector"),
        (30, "HardFault", "FORCED", "Configurable fault escalated to HardFault"),
        (31, "HardFault", "DEBUGEVT", "Debug event caused HardFault"),
    )

    @classmethod
    def decode_flags(cls, cfsr: int, hfsr: int) -> Tuple[FaultFlag, ...]:
        rows = []
        for bit, group, name, description in cls._CFSR_FLAGS:
            if int(cfsr) & (1 << bit):
                rows.append(FaultFlag(group, name, bit, description))
        for bit, group, name, description in cls._HFSR_FLAGS:
            if int(hfsr) & (1 << bit):
                rows.append(FaultFlag(group, name, bit, description))
        return tuple(rows)

    @staticmethod
    def fault_address(cfsr: int, mmfar: int, bfar: int):
        if int(cfsr) & (1 << 15):
            return int(bfar) & 0xFFFFFFFF, "BFAR"
        if int(cfsr) & (1 << 7):
            return int(mmfar) & 0xFFFFFFFF, "MMFAR"
        return None, None

    @staticmethod
    def exception_stack_pointer(exc_return: int, msp: int, psp: int):
        exc_return = int(exc_return) & 0xFFFFFFFF
        used_psp = bool(exc_return & (1 << 2))
        extended = not bool(exc_return & (1 << 4))
        return (int(psp) if used_psp else int(msp)), used_psp, extended

    @classmethod
    def decode_exception_frame(cls, data: bytes, *, stack_pointer: int,
                               used_psp: bool, extended_fp_frame: bool) -> ExceptionFrame:
        core_offset = 18 * 4 if extended_fp_frame else 0
        if len(data) < core_offset + 32:
            raise ValueError("Exception frame data is too short.")
        words = [
            int.from_bytes(data[core_offset + index:core_offset + index + 4], "little")
            for index in range(0, 32, 4)
        ]
        return ExceptionFrame(
            r0=words[0], r1=words[1], r2=words[2], r3=words[3],
            r12=words[4], lr=words[5], pc=words[6], xpsr=words[7],
            stack_pointer=int(stack_pointer), used_psp=bool(used_psp),
            extended_fp_frame=bool(extended_fp_frame),
        )
