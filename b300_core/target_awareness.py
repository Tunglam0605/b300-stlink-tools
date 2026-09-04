"""Facade for the next B300 Target Awareness milestone.

This module intentionally sits beside DebugWorkstationController until the v0.15.2
hotfix is released. It can be attached to the existing halted workspace without
changing Flash, OTA, Live Monitor, SSH, GDB/TCL binding, or target lifecycle code.
"""

from __future__ import annotations

from pathlib import Path

from .debug_memory import DebugMemoryBackend
from .debug_workspace import DebugWorkspaceBackend
from .fault import FaultService
from .peripheral import PeripheralService, SvdLoader
from .target import STM32F407ZE, TargetDescription


class TargetAwarenessFacade:
    """Read-only target/peripheral/fault services for an existing halted session."""

    def __init__(self, workspace: DebugWorkspaceBackend, memory: DebugMemoryBackend,
                 target: TargetDescription = STM32F407ZE) -> None:
        self.workspace = workspace
        self.memory = memory
        self.target = target
        self.peripherals = PeripheralService(memory, target)
        self.faults = FaultService(workspace, memory, target)

    def load_svd(self, path: Path):
        device = SvdLoader.load(path)
        self.peripherals.set_device(device)
        return device

    def target_summary(self) -> dict:
        return {
            "key": self.target.key,
            "vendor": self.target.vendor,
            "family": self.target.family,
            "part": self.target.part,
            "core": self.target.core,
            "flash_bytes": self.target.flash_bytes,
            "breakpoints": self.target.breakpoint_count,
            "watchpoints": self.target.watchpoint_count,
            "capabilities": {
                "dwt": self.target.capabilities.dwt,
                "fpu": self.target.capabilities.fpu,
                "nvic": self.target.capabilities.nvic,
                "scb": self.target.capabilities.scb,
                "svd": self.target.capabilities.svd,
                "swo": self.target.capabilities.swo,
                "itm": self.target.capabilities.itm,
            },
        }
