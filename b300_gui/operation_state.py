"""Derived GUI interlocks for exclusive B300 hardware ownership."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationState:
    main_hardware_busy: bool
    memory_hardware_busy: bool
    debug_hardware_busy: bool = False

    @property
    def is_hardware_busy(self) -> bool:
        return (
            self.main_hardware_busy or self.memory_hardware_busy or
            self.debug_hardware_busy
        )

    @property
    def main_blocked_by_other(self) -> bool:
        return self.memory_hardware_busy or self.debug_hardware_busy

    @property
    def memory_blocked_by_other(self) -> bool:
        return self.main_hardware_busy or self.debug_hardware_busy

    @property
    def debug_blocked_by_other(self) -> bool:
        return self.main_hardware_busy or self.memory_hardware_busy
