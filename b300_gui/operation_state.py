"""Single derived interlock for update installation and GUI shutdown."""

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
