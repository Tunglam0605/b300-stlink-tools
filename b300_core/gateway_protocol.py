"""Versioned command and capability contract for Gateway control over SSH."""

from __future__ import annotations

from b300_version import __version__


GATEWAY_PROTOCOL_VERSION = 1
GATEWAY_STATUS_COMMAND = "b300-stlink debug gateway-status --json"
GATEWAY_ENSURE_COMMAND = "b300-stlink debug gateway-ensure --json"
GATEWAY_RESCAN_COMMAND = "b300-stlink debug gateway-rescan --json"


def gateway_capabilities() -> dict:
    return {
        "protocol_version": GATEWAY_PROTOCOL_VERSION,
        "tool_version": __version__,
        "capabilities": ["gateway-status", "gateway-ensure", "gateway-rescan", "gateway-gdb-activity-v1"],
        "transport": "authenticated-ssh",
        "debug_bind": "loopback-only",
    }


__all__ = [
    "GATEWAY_ENSURE_COMMAND", "GATEWAY_PROTOCOL_VERSION", "GATEWAY_RESCAN_COMMAND",
    "GATEWAY_STATUS_COMMAND", "gateway_capabilities",
]
