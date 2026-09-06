"""Track physical ST-Link attachments separately from probe identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .models import ProbeInfo


@dataclass(frozen=True)
class ProbePresenceEvent:
    kind: str
    generation: int
    probe: Optional[ProbeInfo]
    attachment_id: Optional[str]


class ProbePresenceTracker:
    """Follow one selected probe without silently switching weak identities."""

    def __init__(self, selected_probe: ProbeInfo) -> None:
        self.selected_probe = selected_probe
        self._attachment_id: Optional[str] = None
        self._generation = 0
        self._seen_present = False

    def observe(self, probes: Sequence[ProbeInfo]) -> ProbePresenceEvent:
        available = tuple(probes)
        serial = self.selected_probe.serial
        if serial is not None:
            matches = tuple(probe for probe in available if probe.serial == serial)
            if len(matches) > 1:
                return self._event("AMBIGUOUS", None)
            if not matches:
                reused = any(
                    self._attachment_id is not None
                    and probe.usb_identity == self._attachment_id
                    and probe.serial != serial
                    for probe in available
                )
                return self._event("AMBIGUOUS" if reused else "REMOVED", None)
            candidate = matches[0]
        else:
            expected = self.selected_probe.usb_identity
            matches = tuple(probe for probe in available if probe.usb_identity == expected)
            if len(matches) == 1 and not self._seen_present:
                candidate = matches[0]
            elif len(matches) == 1 and self._attachment_id == expected:
                candidate = matches[0]
            elif not available:
                return self._event("REMOVED", None)
            else:
                return self._event("AMBIGUOUS", None)

        attachment = candidate.usb_identity
        changed = self._seen_present and attachment != self._attachment_id
        if not self._seen_present or changed:
            self._generation += 1
        self._seen_present = True
        self._attachment_id = attachment
        return self._event("REPLACED" if changed else "PRESENT", candidate)

    def _event(self, kind: str, probe: Optional[ProbeInfo]) -> ProbePresenceEvent:
        return ProbePresenceEvent(
            kind, self._generation, probe,
            probe.usb_identity if probe is not None else None,
        )


__all__ = ["ProbePresenceEvent", "ProbePresenceTracker"]
