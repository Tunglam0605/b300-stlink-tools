"""Pure presentation state for the B300 desktop interface."""

from __future__ import annotations

from dataclasses import dataclass

from b300_core.models import FlashPlan


@dataclass(frozen=True)
class FlashViewState:
    target_ready: bool
    image_valid: bool
    busy: bool

    @property
    def can_flash(self) -> bool:
        return self.target_ready and self.image_valid and not self.busy


def confirmation_text(plan: FlashPlan) -> str:
    probe = plan.probe.serial or "Auto-select (single ST-Link)"
    return (
        "Probe: %s\n"
        "Firmware: %s\n"
        "SHA-256: %s\n\n"
        "Erase Sector 3–7, program và verify Application.\n"
        "Sector 0–2 Bootloader sẽ được giữ nguyên.\n\n"
        "Tiếp tục nạp firmware?"
    ) % (probe, plan.image.path.name, plan.image.sha256)
