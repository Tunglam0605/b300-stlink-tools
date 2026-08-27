"""High-level B300 operations shared by CLI and GUI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .hex_image import inspect_image
from .memory import read_memory
from .metadata import OTA_META_SIZE, decode_ota_metadata
from .models import (
    BootVerification,
    CommandResult,
    FlashPlan,
    ImageInfo,
    OtaMetadata,
    ProbeRef,
)
from .openocd import (
    EventSink,
    OpenOcdRunner,
    build_boot_verify_command,
    build_flash_command,
    parse_boot_verification,
    resolve_openocd,
)
from .policy import METADATA_ADDRESS, build_flash_plan, sector_by_index


@dataclass(frozen=True)
class FlashResult:
    status: str
    flash_command: CommandResult
    boot_command: Optional[CommandResult]
    boot_verification: Optional[BootVerification]

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class B300Service:
    def __init__(self, runner: Optional[OpenOcdRunner] = None,
                 executable: Optional[str] = None) -> None:
        self.runner = runner or OpenOcdRunner()
        self.executable = resolve_openocd(executable)

    def doctor(self):
        available = bool(shutil.which(self.executable) or Path(self.executable).is_file())
        return available, self.executable

    def inspect_image(self, path: Path) -> ImageInfo:
        return inspect_image(path)

    def plan(self, image: ImageInfo, probe: ProbeRef) -> FlashPlan:
        return build_flash_plan(image, probe)

    def flash_command(self, plan: FlashPlan):
        return build_flash_command(plan, self.executable)

    def boot_verify_command(self, probe: ProbeRef):
        return build_boot_verify_command(probe, self.executable)

    def verify_boot(self, probe: ProbeRef,
                    event_sink: Optional[EventSink] = None):
        command_result = self.runner.run(
            self.boot_verify_command(probe), event_sink=event_sink
        )
        verification = parse_boot_verification(command_result.output)
        if command_result.returncode != 0:
            verification = BootVerification(
                verification.pc,
                verification.bkp1r,
                verification.bkp4r,
                False,
                "OpenOCD boot verification failed with exit code %d." %
                command_result.returncode,
            )
        return command_result, verification

    def flash(self, plan: FlashPlan,
              event_sink: Optional[EventSink] = None) -> FlashResult:
        flash_result = self.runner.run(
            self.flash_command(plan), event_sink=event_sink
        )
        if flash_result.returncode != 0 or "Verified OK" not in flash_result.output:
            return FlashResult("flash_failed", flash_result, None, None)

        boot_result, verification = self.verify_boot(plan.probe, event_sink=event_sink)
        status = "succeeded" if verification.passed else "programmed_boot_failed"
        return FlashResult(status, flash_result, boot_result, verification)

    def read_sector(self, probe: ProbeRef, sector_index: int,
                    event_sink: Optional[EventSink] = None) -> bytes:
        sector = sector_by_index(sector_index)
        return read_memory(
            probe,
            sector.start_address,
            sector.size,
            executable=self.executable,
            runner=self.runner,
            event_sink=event_sink,
        )

    def read_metadata(self, probe: ProbeRef,
                      event_sink: Optional[EventSink] = None) -> OtaMetadata:
        data = read_memory(
            probe,
            METADATA_ADDRESS,
            OTA_META_SIZE,
            executable=self.executable,
            runner=self.runner,
            event_sink=event_sink,
        )
        return decode_ota_metadata(data)
