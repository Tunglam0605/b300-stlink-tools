"""Visual Pipeline Stepper for Safe STM32F407 Provisioning."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class StepCard(QFrame):
    """Single step element in the provisioning pipeline stepper."""

    def __init__(self, step_num: int, title: str, subtitle: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.step_num = step_num
        self.title_text = title
        self.subtitle_text = subtitle
        self._state = "idle"

        self.setObjectName("stepCard")
        self.setProperty("state", "idle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # Header with circle badge + title
        top_h = QHBoxLayout()
        top_h.setContentsMargins(0, 0, 0, 0)
        top_h.setSpacing(6)

        self.badge = QLabel(str(step_num))
        self.badge.setFixedSize(20, 20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setObjectName("stepBadge")
        top_h.addWidget(self.badge)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("eyebrowLabel")
        top_h.addWidget(self.title_label, 1)

        layout.addLayout(top_h)

        self.sub_label = QLabel(subtitle)
        self.sub_label.setObjectName("pageContextSubtitle")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)

        self.set_state("idle")

    def set_state(self, state: str, message: Optional[str] = None) -> None:
        self._state = state
        if message:
            self.sub_label.setText(message)
        else:
            self.sub_label.setText(self.subtitle_text)

        if state == "active":
            self.badge.setText(str(self.step_num))
        elif state == "success":
            self.badge.setText("OK")
        elif state == "error":
            self.badge.setText("ERR")
        else:
            self.badge.setText(str(self.step_num))

        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class PipelineStepper(QFrame):
    """Container for the 6-step provisioning pipeline."""

    STEPS = [
        (1, "1. ST-Link", "Nhận diện phần cứng"),
        (2, "2. Vector HEX", "Vector & Flash Span"),
        (3, "3. Xóa S3–S7", "Giữ Bootloader S0-S2"),
        (4, "4. Nạp App", "Xác thực byte image"),
        (5, "5. 44B STLM", "Ghi AppMeta Verified"),
        (6, "6. Boot Confirm", "Xác thực PC & BKP1R"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardSurface")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._step_widgets: List[StepCard] = []
        for num, title, sub in self.STEPS:
            card = StepCard(num, title, sub, self)
            self._step_widgets.append(card)
            layout.addWidget(card, 1)

    def reset_steps(self) -> None:
        for card in self._step_widgets:
            card.set_state("idle")

    def set_step_state(self, step_index: int, state: str, message: Optional[str] = None) -> None:
        if 0 <= step_index < len(self._step_widgets):
            self._step_widgets[step_index].set_state(state, message)

    def map_phase(self, phase_name: str, is_error: bool = False, message: Optional[str] = None) -> None:
        phase_to_step = {
            "probe_check": 0,
            "validate_hex": 1,
            "erase_sectors": 2,
            "write_image": 3,
            "verify_image": 3,
            "write_metadata": 4,
            "verify_metadata": 4,
            "boot_verification": 5,
            "succeeded": 5,
            "probe": 0,
            "validate": 1,
            "erase": 2,
            "program": 3,
            "metadata": 4,
            "verify": 4,
            "boot": 5,
        }
        idx = phase_to_step.get(phase_name, 0)
        state = "error" if is_error else ("success" if phase_name == "succeeded" else "active")
        for i in range(idx):
            self.set_step_state(i, "success")
        self.set_step_state(idx, state, message)
