"""Stable streaming and snapshot output for the command-line interface."""

from __future__ import annotations

import json
from typing import Iterable, Mapping

from b300_core.models import ProbeInfo


class Reporter:
    """Legacy event reporter; JSON output remains one line per event."""

    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def emit(self, event: str, **fields: object) -> None:
        record = {"event": event, **fields}
        if self.as_json:
            print(json.dumps(record, sort_keys=True), flush=True)
        else:
            print("[%s] %s" % (event, " ".join(
                "%s=%s" % (key, value) for key, value in fields.items())), flush=True)


def emit_snapshot(record: Mapping[str, object], as_json: bool, text: str) -> None:
    """Emit exactly one snapshot record, or its stable human-readable form."""
    if as_json:
        print(json.dumps(record, sort_keys=True), flush=True)
    else:
        print(text, flush=True)


def probe_record(index: int, probe: ProbeInfo) -> dict:
    return {
        "index": index,
        "name": probe.name,
        "serial": probe.serial,
        "serial_available": probe.serial_available,
        "usb_identity": probe.usb_identity,
        "source": probe.source,
        "status": probe.status,
    }


def format_probes_text(probes: Iterable[ProbeInfo]) -> str:
    lines = []
    for index, probe in enumerate(probes, start=1):
        lines.append(
            "index=%s type/name=%s serial=%s serial_available=%s usb_identity=%s source=%s status=%s" % (
                index,
                probe.name,
                probe.serial,
                probe.serial_available,
                probe.usb_identity,
                probe.source,
                probe.status,
            )
        )
    return "\n".join(lines)
