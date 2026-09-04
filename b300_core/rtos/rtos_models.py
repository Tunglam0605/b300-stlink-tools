"""Immutable RTOS inspector view models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class FreeRtosTask:
    name: str
    state: str
    priority: int
    base_priority: Optional[int]
    tcb_address: int
    stack_pointer: int
    stack_start: Optional[int] = None
    stack_end: Optional[int] = None
    stack_total_bytes: Optional[int] = None
    stack_used_bytes: Optional[int] = None


@dataclass(frozen=True)
class FreeRtosSnapshot:
    current_tcb: Optional[int]
    declared_task_count: Optional[int]
    tasks: Tuple[FreeRtosTask, ...]
    limited_reason: Optional[str] = None

    @property
    def complete(self) -> bool:
        if self.limited_reason:
            return False
        if self.declared_task_count is None:
            return True
        return len(self.tasks) >= self.declared_task_count
