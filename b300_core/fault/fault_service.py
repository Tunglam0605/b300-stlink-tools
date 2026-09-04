"""HALT-only Cortex-M fault capture built on existing read-only debug backends."""

from __future__ import annotations

from typing import Dict, Optional

from b300_core.debug_memory import DebugMemoryBackend
from b300_core.debug_workspace import DebugWorkspaceBackend
from b300_core.target.target_description import TargetDescription
from .cortexm_fault import CortexMFaultDecoder, FaultAnalysis


class FaultService:
    SCB_SHCSR = 0xE000ED24
    SCB_CFSR = 0xE000ED28
    SCB_HFSR = 0xE000ED2C
    SCB_DFSR = 0xE000ED30
    SCB_MMFAR = 0xE000ED34
    SCB_BFAR = 0xE000ED38
    SCB_AFSR = 0xE000ED3C

    def __init__(self, workspace: DebugWorkspaceBackend, memory: DebugMemoryBackend,
                 target: TargetDescription) -> None:
        self.workspace = workspace
        self.memory = memory
        self.target = target

    @staticmethod
    def _parse_register(value: str) -> Optional[int]:
        text = str(value).strip().split()[0]
        try:
            return int(text, 0) & 0xFFFFFFFF
        except (ValueError, IndexError):
            return None

    def _core_registers(self) -> Dict[str, int]:
        values = {}
        for register in self.workspace.registers():
            parsed = self._parse_register(register.value)
            if parsed is not None:
                values[register.name.lower()] = parsed
        return values

    def capture(self) -> FaultAnalysis:
        if self.memory.target_state != "halted":
            raise RuntimeError("Fault Analyzer requires a HALTED target.")
        block = self.memory.read(self.SCB_SHCSR, 28)
        words = [int.from_bytes(block.data[index:index + 4], "little") for index in range(0, 28, 4)]
        shcsr, cfsr, hfsr, dfsr, mmfar, bfar, afsr = words
        registers = self._core_registers()
        frame = None
        exc_return = registers.get("lr")
        msp = registers.get("msp")
        psp = registers.get("psp")
        if exc_return is not None and msp is not None and psp is not None and (exc_return & 0xFF000000) == 0xFF000000:
            stack_pointer, used_psp, extended = CortexMFaultDecoder.exception_stack_pointer(exc_return, msp, psp)
            frame_len = 104 if extended else 32
            region = self.target.classify_address(stack_pointer, frame_len)
            if region is not None and region.kind == "ram":
                frame_data = self.memory.read(stack_pointer, frame_len).data
                frame = CortexMFaultDecoder.decode_exception_frame(
                    frame_data,
                    stack_pointer=stack_pointer,
                    used_psp=used_psp,
                    extended_fp_frame=extended,
                )
        address, address_source = CortexMFaultDecoder.fault_address(cfsr, mmfar, bfar)
        return FaultAnalysis(
            cfsr=cfsr,
            hfsr=hfsr,
            dfsr=dfsr,
            afsr=afsr,
            mmfar=mmfar,
            bfar=bfar,
            shcsr=shcsr,
            flags=CortexMFaultDecoder.decode_flags(cfsr, hfsr),
            fault_address=address,
            fault_address_source=address_source,
            exception_frame=frame,
        )

    def classify_fault_address(self, analysis: FaultAnalysis) -> Optional[str]:
        if analysis.fault_address is None:
            return None
        region = self.target.classify_address(analysis.fault_address)
        return None if region is None else region.name
