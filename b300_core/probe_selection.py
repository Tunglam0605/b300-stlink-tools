"""Safe selection of one physically discovered ST-Link probe."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import ProbeInfo, ProbeRef


class ProbeSelectionError(ValueError):
    """A stable, operator-facing reason why a probe cannot be selected."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def select_probe(probes: Sequence[ProbeInfo], requested_serial: Optional[str]) \
        -> Tuple[ProbeInfo, ProbeRef]:
    """Return exactly one probe, never turning a USB identity into a serial."""
    available = tuple(probes)
    if not available:
        raise ProbeSelectionError("NO_PROBE", "No ST-Link probe was found.")

    if requested_serial is not None:
        for probe in available:
            if probe.serial == requested_serial:
                return probe, ProbeRef(probe.serial)
        raise ProbeSelectionError(
            "PROBE_NOT_FOUND",
            "The requested ST-Link probe serial was not found.",
        )

    if len(available) == 1:
        probe = available[0]
        return probe, ProbeRef(probe.serial)

    if all(probe.serial is None for probe in available):
        raise ProbeSelectionError(
            "UNPINNABLE_MULTIPLE_PROBES",
            "multiple ST-Link probes were found, but none has a usable serial.",
        )
    raise ProbeSelectionError(
        "MULTIPLE_PROBES",
        "multiple ST-Link probes were found; select one with --probe-serial.",
    )
