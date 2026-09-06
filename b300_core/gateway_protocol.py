"""Versioned command and capability contract for Gateway control over SSH."""

from __future__ import annotations


GATEWAY_PROTOCOL_VERSION = 1
GATEWAY_STATUS_COMMAND = "b300-stlink debug gateway-status --json"
GATEWAY_ENSURE_COMMAND = "b300-stlink debug gateway-ensure --json"
GATEWAY_RESCAN_COMMAND = "b300-stlink debug gateway-rescan --json"


def gateway_capabilities() -> dict:
    return {
        "protocol_version": GATEWAY_PROTOCOL_VERSION,
        "capabilities": ["gateway-status", "gateway-ensure", "gateway-rescan"],
        "transport": "authenticated-ssh",
        "debug_bind": "loopback-only",
    }


__all__ = [
    "GATEWAY_ENSURE_COMMAND", "GATEWAY_PROTOCOL_VERSION", "GATEWAY_RESCAN_COMMAND",
    "GATEWAY_STATUS_COMMAND", "gateway_capabilities",
]
