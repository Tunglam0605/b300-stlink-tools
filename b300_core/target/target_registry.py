"""Small explicit registry for supported MCU targets."""

from __future__ import annotations

from typing import Dict, Iterable

from .target_description import TargetDescription
from .stm32f407ze import STM32F407ZE


class TargetRegistry:
    def __init__(self, targets: Iterable[TargetDescription] = ()) -> None:
        self._targets: Dict[str, TargetDescription] = {}
        for target in targets:
            self.register(target)

    def register(self, target: TargetDescription) -> None:
        key = target.key.strip().lower()
        if not key:
            raise ValueError("Target key cannot be empty.")
        self._targets[key] = target

    def get(self, key: str) -> TargetDescription:
        selected = str(key).strip().lower()
        try:
            return self._targets[selected]
        except KeyError as error:
            raise KeyError("Unsupported debug target: %s" % key) from error

    def all(self):
        return tuple(self._targets[key] for key in sorted(self._targets))


def default_registry() -> TargetRegistry:
    return TargetRegistry((STM32F407ZE,))
