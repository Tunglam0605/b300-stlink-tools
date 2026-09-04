"""Optional Python/C++ bridge for B300 native debug data-plane.

The adapter never owns target-control or Flash/OTA policy. It normalizes the
native extension into stable Python DTOs and retains a Python fallback so the
application remains usable when the native module is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Iterable, Optional, Tuple


_NATIVE_MODULE = "_b300_debug_core"
_NATIVE_ABI_VERSION = 1
_VALID_MODES = {"auto", "off", "on"}
_TRACE_TYPE_SAMPLE = 6


class NativeCoreUnavailable(RuntimeError):
    """Raised when mode=on requires a missing or incompatible native module."""


@dataclass(frozen=True)
class TraceEventDTO:
    timestamp_ns: int
    source_id: int
    channel: int
    type: int
    value: int


@dataclass(frozen=True)
class DecodeResult:
    consumed: int
    events: Tuple[TraceEventDTO, ...]


def _python_decode_fixed_width(
    payload: bytes,
    *,
    channel: int,
    timestamp_ns: int,
    source_id: int,
) -> DecodeResult:
    width = 4
    count = len(payload) // width
    events = []
    for index in range(count):
        offset = index * width
        value = int.from_bytes(payload[offset : offset + width], "little", signed=False)
        events.append(
            TraceEventDTO(
                timestamp_ns=timestamp_ns + index,
                source_id=source_id,
                channel=channel,
                type=_TRACE_TYPE_SAMPLE,
                value=value,
            )
        )
    return DecodeResult(consumed=count * width, events=tuple(events))


def _load_native_module() -> Optional[Any]:
    try:
        module = import_module(_NATIVE_MODULE)
    except (ImportError, OSError):
        return None
    if int(getattr(module, "ABI_VERSION", -1)) != _NATIVE_ABI_VERSION:
        return None
    return module


def _normalize_native_result(raw: Any) -> DecodeResult:
    if not isinstance(raw, dict):
        raise RuntimeError("native debug core returned an invalid result mapping")
    consumed = int(raw.get("consumed", -1))
    raw_events: Iterable[Any] = raw.get("events", ())
    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            raise RuntimeError("native debug core returned an invalid event mapping")
        events.append(
            TraceEventDTO(
                timestamp_ns=int(item["timestamp_ns"]),
                source_id=int(item["source_id"]),
                channel=int(item["channel"]),
                type=int(item["type"]),
                value=int(item["value"]),
            )
        )
    if consumed < 0:
        raise RuntimeError("native debug core returned an invalid consumed count")
    return DecodeResult(consumed=consumed, events=tuple(events))


class NativeDebugCoreAdapter:
    """Select native or Python data-plane without changing safety ownership."""

    def __init__(self, mode: str = "auto", native_module: Optional[Any] = None) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in _VALID_MODES:
            raise ValueError("native debug mode must be one of: auto, off, on")

        self._mode = normalized
        self._native = None if normalized == "off" else (native_module or _load_native_module())
        if normalized == "on" and self._native is None:
            raise NativeCoreUnavailable("native debug core is required but unavailable or ABI-incompatible")

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def native_available(self) -> bool:
        return self._native is not None

    @property
    def backend(self) -> str:
        return "native" if self._native is not None else "python"

    def decode_fixed_width(
        self,
        payload: bytes,
        *,
        channel: int,
        timestamp_ns: int,
        source_id: int,
    ) -> DecodeResult:
        data = bytes(payload)
        if self._native is None:
            return _python_decode_fixed_width(
                data,
                channel=channel,
                timestamp_ns=timestamp_ns,
                source_id=source_id,
            )

        raw = self._native.decode_fixed_width(data, channel, timestamp_ns, source_id)
        return _normalize_native_result(raw)


__all__ = [
    "DecodeResult",
    "NativeCoreUnavailable",
    "NativeDebugCoreAdapter",
    "TraceEventDTO",
]
