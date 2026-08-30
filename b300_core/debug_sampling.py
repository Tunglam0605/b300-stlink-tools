"""Bounded variable sampling primitives for debug diagnostics and future live plots."""

from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple


MAX_EXPRESSIONS = 16
MAX_SAMPLE_CYCLES = 1000
MIN_SAMPLE_INTERVAL_SECONDS = 0.1
MAX_SAMPLE_INTERVAL_SECONDS = 60.0

_DECIMAL_INTEGER = re.compile(r"^[+-]?[0-9]+$")
_HEX_INTEGER = re.compile(r"^[+-]?0[xX][0-9A-Fa-f]+$")
_FLOAT_VALUE = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$"
)


@dataclass(frozen=True)
class VariableSample:
    cycle: int
    elapsed_seconds: float
    captured_at_unix_ms: int
    expression: str
    raw_value: str
    numeric_value: Optional[float]

    def to_record(self) -> dict:
        return asdict(self)


def parse_numeric_value(raw_value: str) -> Optional[float]:
    """Return a numeric representation only when the GDB value is unambiguous."""
    text = str(raw_value).strip()
    lowered = text.lower()
    if lowered == "true":
        return 1.0
    if lowered == "false":
        return 0.0
    try:
        if _HEX_INTEGER.fullmatch(text):
            return float(int(text, 0))
        if _DECIMAL_INTEGER.fullmatch(text):
            return float(int(text, 10))
        if _FLOAT_VALUE.fullmatch(text):
            value = float(text)
            return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None
    return None


def validate_sampling_request(
    expressions: Sequence[str], sample_cycles: int, interval_seconds: float
) -> Tuple[str, ...]:
    normalized = tuple(str(item).strip() for item in expressions if str(item).strip())
    if not normalized:
        raise ValueError("At least one variable expression is required for sampling.")
    if len(normalized) > MAX_EXPRESSIONS:
        raise ValueError("Sampling supports at most %d expressions per cycle." % MAX_EXPRESSIONS)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Sampling expressions must be unique.")
    if not 1 <= int(sample_cycles) <= MAX_SAMPLE_CYCLES:
        raise ValueError("Sample cycles must be in range 1..%d." % MAX_SAMPLE_CYCLES)
    interval = float(interval_seconds)
    if not MIN_SAMPLE_INTERVAL_SECONDS <= interval <= MAX_SAMPLE_INTERVAL_SECONDS:
        raise ValueError(
            "Sample interval must be in range %.1f..%.1f seconds." %
            (MIN_SAMPLE_INTERVAL_SECONDS, MAX_SAMPLE_INTERVAL_SECONDS)
        )
    return normalized


def sample_variables(
    capture: Callable[[Sequence[str]], Sequence[object]],
    expressions: Sequence[str],
    sample_cycles: int,
    interval_seconds: float,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> Tuple[VariableSample, ...]:
    """Capture bounded samples without creating catch-up bursts when a cycle is slow."""
    selected = validate_sampling_request(expressions, sample_cycles, interval_seconds)
    start = monotonic()
    samples = []
    for cycle in range(int(sample_cycles)):
        if cycle:
            sleeper(float(interval_seconds))
        values = tuple(capture(selected))
        if len(values) != len(selected):
            raise RuntimeError(
                "Debug sampler captured %d values for %d expressions." %
                (len(values), len(selected))
            )
        captured_at = int(round(wall_clock() * 1000.0))
        elapsed = max(0.0, monotonic() - start)
        for expected, value in zip(selected, values):
            expression = str(getattr(value, "expression", expected))
            raw_value = str(getattr(value, "value", value))
            if expression != expected:
                raise RuntimeError(
                    "Debug sampler expression mismatch: expected %s, received %s." %
                    (expected, expression)
                )
            samples.append(VariableSample(
                cycle=cycle,
                elapsed_seconds=elapsed,
                captured_at_unix_ms=captured_at,
                expression=expression,
                raw_value=raw_value,
                numeric_value=parse_numeric_value(raw_value),
            ))
    return tuple(samples)


def write_samples(path: Path, samples: Sequence[VariableSample]) -> Path:
    destination = Path(path).expanduser().resolve()
    suffix = destination.suffix.lower()
    if suffix not in {".csv", ".jsonl"}:
        raise ValueError("Sample output must use .csv or .jsonl.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "cycle", "elapsed_seconds", "captured_at_unix_ms",
                "expression", "raw_value", "numeric_value",
            ))
            writer.writeheader()
            for sample in samples:
                writer.writerow(sample.to_record())
    else:
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            for sample in samples:
                stream.write(json.dumps(sample.to_record(), ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
    return destination
