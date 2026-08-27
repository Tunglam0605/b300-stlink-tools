"""High-level B300 operations shared by CLI and GUI."""

from __future__ import annotations

import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from .hex_image import inspect_image
from .memory import read_memory
from .metadata import OTA_META_SIZE, decode_ota_metadata
from .models import (
    BootVerification,
    CommandResult,
    FlashPlan,
    FlashPhaseEvent,
    ImageInfo,
    OtaMetadata,
    ProbeRef,
    TargetInfo,
)
from .openocd import (
    EventSink,
    OpenOcdRunner,
    build_boot_verify_command,
    build_flash_command,
    build_marker_command,
    build_reset_command,
    build_resume_command,
    build_target_inspect_command,
    parse_boot_verification,
    parse_target_info,
    program_verify_succeeded,
    resolve_openocd,
)
from .policy import (
    METADATA_ADDRESS,
    build_flash_plan,
    build_flash_preview,
    sector_by_index,
    validate_target_for_provisioning,
)


class ProvisioningError(ValueError):
    """Structured pre-destructive failure safe to surface in CLI and GUI."""

    def __init__(self, phase: str, reason: str, next_action: str) -> None:
        super().__init__(reason)
        self.phase = phase
        self.reason = reason
        self.next_action = next_action


@dataclass(frozen=True)
class FlashResult:
    status: str
    flash_command: CommandResult
    marker_command: Optional[CommandResult]
    reset_command: Optional[CommandResult]
    boot_command: Optional[CommandResult]
    boot_verification: Optional[BootVerification]
    failure_phase: Optional[str] = None
    reason: str = ""
    next_action: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class B300Service:
    def __init__(self, runner: Optional[OpenOcdRunner] = None,
                 executable: Optional[str] = None) -> None:
        self.runner = runner or OpenOcdRunner()
        self.executable = resolve_openocd(executable)
        self._operation_lock = threading.RLock()

    @contextmanager
    def _exclusive_hardware_operation(self):
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("Another ST-Link operation is already running.")
        try:
            yield
        finally:
            self._operation_lock.release()

    def doctor(self):
        available = bool(shutil.which(self.executable) or Path(self.executable).is_file())
        return available, self.executable

    def inspect_image(self, path: Path) -> ImageInfo:
        return inspect_image(path)

    def preview_plan(self, image: ImageInfo, probe: ProbeRef):
        return build_flash_preview(image, probe)

    def plan(self, image: ImageInfo, probe: ProbeRef,
             target: TargetInfo) -> FlashPlan:
        return build_flash_plan(image, probe, target)

    def flash_command(self, plan: FlashPlan):
        return build_flash_command(plan, self.executable)

    def marker_command(self, probe: ProbeRef):
        return build_marker_command(probe, self.executable)

    def reset_command(self, probe: ProbeRef):
        return build_reset_command(probe, self.executable)

    def boot_verify_command(self, probe: ProbeRef):
        return build_boot_verify_command(probe, self.executable)

    def inspect_target(self, probe: ProbeRef,
                       event_sink: Optional[EventSink] = None,
                       cancel_event: Optional[threading.Event] = None):
        with self._exclusive_hardware_operation():
            result = self.runner.run(
                build_target_inspect_command(probe, self.executable),
                event_sink=event_sink,
                timeout_seconds=20.0,
                cancel_event=cancel_event,
            )
            if result.returncode != 0:
                if result.timed_out:
                    raise RuntimeError("OpenOCD target inspection timed out.")
                if result.cancelled:
                    raise RuntimeError("OpenOCD target inspection was cancelled.")
                raise RuntimeError("OpenOCD target inspection failed: %s" % result.output)
            return parse_target_info(result.output)

    def verify_boot(self, probe: ProbeRef,
                    event_sink: Optional[EventSink] = None,
                    cancel_event: Optional[threading.Event] = None):
        with self._exclusive_hardware_operation():
            command_result = self.runner.run(
                self.boot_verify_command(probe), event_sink=event_sink,
                timeout_seconds=20.0,
                cancel_event=cancel_event,
            )
            verification = parse_boot_verification(command_result.output)
            if command_result.returncode != 0:
                recovery = None
                if command_result.timed_out or command_result.cancelled:
                    recovery = self.runner.run(
                        build_resume_command(probe, self.executable),
                        event_sink=event_sink,
                        timeout_seconds=20.0,
                    )
                if command_result.timed_out:
                    reason = "OpenOCD boot verification timed out; resume recovery was requested."
                elif command_result.cancelled:
                    reason = "OpenOCD boot verification was cancelled; resume recovery was requested."
                else:
                    reason = "OpenOCD boot verification failed with exit code %d." % \
                        command_result.returncode
                if recovery is not None and recovery.returncode != 0:
                    reason += " Resume recovery failed: %s" % recovery.output
                verification = BootVerification(
                    verification.pc,
                    verification.bkp1r,
                    verification.bkp4r,
                    False,
                    reason,
                )
            return command_result, verification

    def flash(self, plan: FlashPlan,
              event_sink: Optional[EventSink] = None,
              phase_sink: Optional[Callable[[FlashPhaseEvent], None]] = None,
              cancel_event: Optional[threading.Event] = None) -> FlashResult:
        with self._exclusive_hardware_operation():
            last_phase = None

            def emit_phase(phase: str, progress: int, message: str,
                           cancellable: bool = False) -> None:
                nonlocal last_phase
                if phase == last_phase:
                    return
                last_phase = phase
                if phase_sink is not None:
                    phase_sink(FlashPhaseEvent(phase, progress, message, cancellable))

            def emit_log(line: str) -> None:
                if event_sink is not None:
                    event_sink(line)
                normalized = line.strip()
                if normalized == "** Programming Started **":
                    emit_phase("programming", 40, "Programming Application")
                elif normalized == "** Verify Started **":
                    emit_phase("verifying", 60, "Verifying Application")

            emit_phase(
                "validating", 0, "Validating approved Application HEX", True
            )
            with tempfile.TemporaryDirectory(prefix="b300-stlink-") as directory:
                staged_path = Path(directory) / "application.hex"
                try:
                    staged_path.write_bytes(plan.image.path.read_bytes())
                    staged_image = inspect_image(staged_path)
                except (OSError, ValueError) as error:
                    emit_phase("failed", 0, "Application validation failed")
                    raise ProvisioningError(
                        "validating",
                        "Application HEX changed after approval or cannot be staged: %s" % error,
                        "Select and validate the intended Application HEX again.",
                    ) from error

                approved = plan.image
                approved_fields = (
                    approved.sha256,
                    approved.start_address,
                    approved.end_address,
                    approved.size,
                    approved.data_record_count,
                )
                staged_fields = (
                    staged_image.sha256,
                    staged_image.start_address,
                    staged_image.end_address,
                    staged_image.size,
                    staged_image.data_record_count,
                )
                if staged_fields != approved_fields:
                    emit_phase("failed", 0, "Approved image no longer matches")
                    raise ProvisioningError(
                        "validating",
                        "Staged Application HEX does not match approved plan.",
                        "Select the HEX again and confirm its new SHA-256.",
                    )

                emit_phase(
                    "target_check", 10, "Checking B300 STM32F407 target", True
                )
                try:
                    target = self.inspect_target(
                        plan.probe, event_sink=emit_log, cancel_event=cancel_event
                    )
                    validate_target_for_provisioning(target)
                except (RuntimeError, ValueError) as error:
                    emit_phase("failed", 10, "Target check failed")
                    raise ProvisioningError(
                        "target_check",
                        str(error),
                        "Check the selected ST-Link serial, cable, power, and F407 target.",
                    ) from error
                if ((target.device_id & 0xFFF) != (plan.target.device_id & 0xFFF) or
                        target.flash_kib != plan.target.flash_kib):
                    emit_phase("failed", 10, "Target changed after approval")
                    raise ProvisioningError(
                        "target_check",
                        "Target changed after flash plan approval; refusing erase.",
                        "Inspect the intended target again and create a new flash plan.",
                    )
                if cancel_event is not None and cancel_event.is_set():
                    emit_phase("failed", 10, "Provisioning cancelled before erase")
                    raise ProvisioningError(
                        "target_check",
                        "Provisioning was cancelled safely before erase.",
                        "No flash data changed; start a new transaction when ready.",
                    )

                staged_plan = replace(plan, image=staged_image)
                emit_phase("erasing", 20, "Erasing Sector 3 through 7")
                flash_result = self.runner.run(
                    self.flash_command(staged_plan), event_sink=emit_log,
                    timeout_seconds=180.0,
                )
                if (flash_result.returncode != 0 or
                        not program_verify_succeeded(flash_result.output)):
                    failure_phase = last_phase or "erasing"
                    if flash_result.timed_out:
                        reason = "OpenOCD timed out during %s." % failure_phase
                    elif flash_result.cancelled:
                        reason = "OpenOCD was cancelled during %s." % failure_phase
                    elif flash_result.returncode != 0:
                        reason = "OpenOCD failed during %s with exit code %d." % (
                            failure_phase, flash_result.returncode
                        )
                    else:
                        reason = "OpenOCD did not report the exact verified-success event."
                    emit_phase("failed", 60, "Program or verify failed; no retry")
                    return FlashResult(
                        status="flash_failed",
                        flash_command=flash_result,
                        marker_command=None,
                        reset_command=None,
                        boot_command=None,
                        boot_verification=None,
                        failure_phase=failure_phase,
                        reason=reason,
                        next_action="Review the OpenOCD log, correct the cause, then start a new transaction manually.",
                    )

                emit_phase("verifying", 60, "Verifying Application")
                emit_phase("marking", 75, "Writing ST-Link provisioning marker")
                marker_result = self.runner.run(
                    self.marker_command(plan.probe),
                    event_sink=emit_log,
                    timeout_seconds=20.0,
                )
                if marker_result.returncode != 0:
                    emit_phase("failed", 75, "Provisioning marker write failed; no retry")
                    return FlashResult(
                        status="flash_failed",
                        flash_command=flash_result,
                        marker_command=marker_result,
                        reset_command=None,
                        boot_command=None,
                        boot_verification=None,
                        failure_phase="marking",
                        reason="OpenOCD could not write the provisioning marker.",
                        next_action="Review the log and reconnect/reset the target before a new manual transaction.",
                    )

                emit_phase("resetting", 85, "Resetting target")
                reset_result = self.runner.run(
                    self.reset_command(plan.probe), event_sink=emit_log,
                    timeout_seconds=20.0,
                )
                if reset_result.returncode != 0:
                    emit_phase("failed", 85, "Target reset failed; no retry")
                    return FlashResult(
                        status="flash_failed",
                        flash_command=flash_result,
                        marker_command=marker_result,
                        reset_command=reset_result,
                        boot_command=None,
                        boot_verification=None,
                        failure_phase="resetting",
                        reason="OpenOCD could not reset the target after writing the marker.",
                        next_action="Power-cycle or reset the board, then inspect boot state; do not retry automatically.",
                    )

                emit_phase("post_verifying", 90, "Verifying Application boot state")
                boot_result, verification = self.verify_boot(
                    plan.probe, event_sink=emit_log
                )
                status = "succeeded" if verification.passed else "programmed_boot_failed"
                if verification.passed:
                    emit_phase("succeeded", 100, "Application is running")
                else:
                    emit_phase("failed", 90, verification.reason)
                return FlashResult(
                    status=status,
                    flash_command=flash_result,
                    marker_command=marker_result,
                    reset_command=reset_result,
                    boot_command=boot_result,
                    boot_verification=verification,
                    failure_phase=None if verification.passed else "post_verifying",
                    reason="" if verification.passed else verification.reason,
                    next_action=("" if verification.passed else
                                 "Inspect Bootloader/metadata and logs; do not flash again automatically."),
                )

    def read_sector(self, probe: ProbeRef, sector_index: int,
                    event_sink: Optional[EventSink] = None,
                    cancel_event: Optional[threading.Event] = None) -> bytes:
        with self._exclusive_hardware_operation():
            sector = sector_by_index(sector_index)
            return read_memory(
                probe,
                sector.start_address,
                sector.size,
                executable=self.executable,
                runner=self.runner,
                event_sink=event_sink,
                cancel_event=cancel_event,
            )

    def read_metadata(self, probe: ProbeRef,
                      event_sink: Optional[EventSink] = None,
                      cancel_event: Optional[threading.Event] = None) -> OtaMetadata:
        with self._exclusive_hardware_operation():
            data = read_memory(
                probe,
                METADATA_ADDRESS,
                OTA_META_SIZE,
                executable=self.executable,
                runner=self.runner,
                event_sink=event_sink,
                cancel_event=cancel_event,
            )
            return decode_ota_metadata(data)
