"""Validated project-sidecar policies for display and monitor thresholds."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union


WATCH_POLICY_SCHEMA_VERSION = 1
MAX_WATCH_POLICIES = 512
MAX_SIDECAR_BYTES = 1024 * 1024
_DISPLAY_FORMATS = frozenset(("decimal", "hex", "binary", "float"))
DEFAULT_WATCH_POLICY_GROUP = "General"


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Watch policy %s must be numeric." % field)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Watch policy %s must be finite." % field)
    return number


@dataclass(frozen=True)
class WatchPolicy:
    """Immutable presentation and alert settings addressed by a watch path."""

    path: str
    group: str = DEFAULT_WATCH_POLICY_GROUP
    display_format: str = "decimal"
    unit: Optional[str] = None
    scale: float = 1.0
    offset: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    capture: bool = False

    def __post_init__(self) -> None:
        path = str(self.path).strip()
        if not path:
            raise ValueError("Watch policy path must not be empty.")
        group = str(self.group).strip()
        if not group:
            raise ValueError("Watch policy group must not be empty.")
        display_format = str(self.display_format).strip().lower()
        if display_format not in _DISPLAY_FORMATS:
            raise ValueError("Watch policy display format must be one of %s." % ", ".join(sorted(_DISPLAY_FORMATS)))
        unit = None if self.unit is None else str(self.unit).strip()
        if unit == "":
            unit = None
        scale = _finite_number(self.scale, "scale")
        offset = _finite_number(self.offset, "offset")
        minimum = None if self.minimum is None else _finite_number(self.minimum, "minimum")
        maximum = None if self.maximum is None else _finite_number(self.maximum, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("Watch policy minimum must not exceed maximum.")
        if not isinstance(self.capture, bool):
            raise ValueError("Watch policy capture must be boolean.")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "group", group)
        object.__setattr__(self, "display_format", display_format)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "scale", scale)
        object.__setattr__(self, "offset", offset)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def engineering_value(self, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        number = float(value)
        if not math.isfinite(number):
            return value
        return number * self.scale + self.offset


def format_watch_value(policy: WatchPolicy, value: object) -> str:
    if not isinstance(policy, WatchPolicy):
        raise TypeError("policy must be a WatchPolicy.")
    transformed = policy.engineering_value(value)
    if isinstance(transformed, bool) or not isinstance(transformed, (int, float)):
        text = str(transformed)
    elif policy.display_format == "float":
        text = str(float(transformed))
    elif policy.display_format in {"hex", "binary"}:
        if not float(transformed).is_integer():
            raise ValueError("Hex and binary display require an integral engineering value.")
        text = (hex if policy.display_format == "hex" else bin)(int(transformed))
    elif float(transformed).is_integer():
        text = str(int(transformed))
    else:
        text = str(float(transformed))
    return "%s %s" % (text, policy.unit) if policy.unit else text


def _policy_record(policy: WatchPolicy) -> dict:
    return {
        "path": policy.path, "group": policy.group, "display_format": policy.display_format, "unit": policy.unit,
        "scale": policy.scale, "offset": policy.offset, "minimum": policy.minimum,
        "maximum": policy.maximum, "capture": policy.capture,
    }


def _parse_policy(record: object) -> WatchPolicy:
    if not isinstance(record, dict):
        raise ValueError("Watch policy must be an object.")
    allowed = {"path", "group", "display_format", "unit", "scale", "offset", "minimum", "maximum", "capture"}
    unknown = set(record) - allowed
    if unknown:
        raise ValueError("Watch policy contains unknown fields: %s." % ", ".join(sorted(unknown)))
    if "path" not in record:
        raise ValueError("Watch policy requires path.")
    return WatchPolicy(**record)


def _validated_policies(policies: Iterable[WatchPolicy]) -> Tuple[WatchPolicy, ...]:
    selected = tuple(policies)
    if len(selected) > MAX_WATCH_POLICIES:
        raise ValueError("At most %d watch policies are allowed." % MAX_WATCH_POLICIES)
    paths = set()
    for policy in selected:
        if not isinstance(policy, WatchPolicy):
            raise ValueError("Watch policies must be WatchPolicy values.")
        if policy.path in paths:
            raise ValueError("Watch policy path is duplicated: %s." % policy.path)
        paths.add(policy.path)
    return selected


def save_watch_policies(path: Union[str, Path], policies: Iterable[WatchPolicy]) -> Path:
    selected = _validated_policies(policies)
    payload = {"schema_version": WATCH_POLICY_SCHEMA_VERSION, "policies": [_policy_record(item) for item in selected]}
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_SIDECAR_BYTES:
        raise ValueError("Watch policy sidecar exceeds the size limit.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def load_watch_policies(path: Union[str, Path]) -> Tuple[WatchPolicy, ...]:
    target = Path(path)
    if not target.is_file():
        raise ValueError("Watch policy sidecar not found: %s" % target)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("Unable to read watch policy sidecar: %s" % exc) from exc
    if len(raw) > MAX_SIDECAR_BYTES:
        raise ValueError("Watch policy sidecar exceeds the size limit.")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid watch policy sidecar JSON: %s" % exc) from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "policies"}:
        raise ValueError("Watch policy sidecar has unknown or missing root fields.")
    if document["schema_version"] != WATCH_POLICY_SCHEMA_VERSION:
        raise ValueError("Unsupported watch policy schema version.")
    if not isinstance(document["policies"], list):
        raise ValueError("Watch policy sidecar policies must be a list.")
    return _validated_policies(_parse_policy(item) for item in document["policies"])


def watch_policy_groups(policies: Iterable[WatchPolicy]) -> Tuple[str, ...]:
    """Return deterministic display groups for a project's watch policies."""
    selected = _validated_policies(policies)
    return tuple(sorted({policy.group for policy in selected}, key=str.casefold))


def watch_policies_for_group(policies: Iterable[WatchPolicy], group: str) -> Tuple[WatchPolicy, ...]:
    """Filter a project's policies without requiring callers to parse sidecar JSON."""
    selected = str(group).strip()
    if not selected:
        raise ValueError("Watch policy group must not be empty.")
    return tuple(policy for policy in _validated_policies(policies) if policy.group == selected)
