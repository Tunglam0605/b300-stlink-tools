"""Thread-safe analytics and bounded history for non-halting Live Monitor samples."""

from __future__ import annotations

import math
import threading
from collections import Counter, deque
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .live_monitor import LiveSample, LiveValue


MIN_HISTORY_CAPACITY = 100
MAX_HISTORY_CAPACITY = 100000
DEFAULT_HISTORY_CAPACITY = 5000


@dataclass(frozen=True)
class LiveFunctionStat:
    function: str
    file: Optional[str]
    line: Optional[int]
    samples: int
    share: float


@dataclass(frozen=True)
class LiveVariableStat:
    name: str
    value_type: str
    address: int
    samples: int
    coherent_samples: int
    incoherent_samples: int
    numeric_samples: int
    latest_value: object
    minimum: Optional[float]
    maximum: Optional[float]
    mean: Optional[float]


@dataclass(frozen=True)
class LiveTimingStats:
    total_samples: int
    retained_samples: int
    capacity: int
    overruns: int
    unknown_source_samples: int
    incoherent_values: int
    mean_read_duration_seconds: float
    max_read_duration_seconds: float
    mean_schedule_lag_seconds: float
    max_schedule_lag_seconds: float


@dataclass(frozen=True)
class LiveExecutionTransition:
    index: int
    start_elapsed_seconds: float
    end_elapsed_seconds: float
    samples: int
    pc: int
    function: Optional[str]
    file: Optional[str]
    line: Optional[int]


@dataclass(frozen=True)
class LiveSeriesPoint:
    elapsed_seconds: float
    value: Optional[float]
    coherent: bool
    raw_hex: str


@dataclass(frozen=True)
class LiveAnalyticsSnapshot:
    timing: LiveTimingStats
    functions: Tuple[LiveFunctionStat, ...]
    variables: Tuple[LiveVariableStat, ...]
    latest_sample: Optional[LiveSample]


@dataclass
class _VariableAccumulator:
    value_type: str
    address: int
    samples: int = 0
    coherent_samples: int = 0
    incoherent_samples: int = 0
    numeric_samples: int = 0
    numeric_sum: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    latest_value: object = None

    def add(self, value: LiveValue) -> None:
        self.samples += 1
        self.latest_value = value.value
        if value.coherent:
            self.coherent_samples += 1
        else:
            self.incoherent_samples += 1
        numeric = _numeric(value.value) if value.coherent else None
        if numeric is None:
            return
        self.numeric_samples += 1
        self.numeric_sum += numeric
        self.minimum = numeric if self.minimum is None else min(self.minimum, numeric)
        self.maximum = numeric if self.maximum is None else max(self.maximum, numeric)

    def snapshot(self, name: str) -> LiveVariableStat:
        mean = self.numeric_sum / self.numeric_samples if self.numeric_samples else None
        return LiveVariableStat(
            name=name, value_type=self.value_type, address=self.address, samples=self.samples,
            coherent_samples=self.coherent_samples, incoherent_samples=self.incoherent_samples,
            numeric_samples=self.numeric_samples, latest_value=self.latest_value,
            minimum=self.minimum, maximum=self.maximum, mean=mean,
        )


def _numeric(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _source_key(sample: LiveSample):
    return sample.source.function, sample.source.file, sample.source.line


class LiveMonitorStore:
    """Bounded sample history plus whole-run statistics safe for GUI cross-thread reads."""

    def __init__(self, capacity: int = DEFAULT_HISTORY_CAPACITY) -> None:
        if not MIN_HISTORY_CAPACITY <= int(capacity) <= MAX_HISTORY_CAPACITY:
            raise ValueError(
                "Live Monitor history capacity must be in range %d..%d." %
                (MIN_HISTORY_CAPACITY, MAX_HISTORY_CAPACITY)
            )
        self.capacity = int(capacity)
        self._lock = threading.RLock()
        self._samples = deque(maxlen=self.capacity)
        self._transitions = deque(maxlen=self.capacity)
        self._reset_totals()

    def _reset_totals(self) -> None:
        self._total_samples = 0
        self._overruns = 0
        self._unknown_source_samples = 0
        self._incoherent_values = 0
        self._read_duration_sum = 0.0
        self._read_duration_max = 0.0
        self._schedule_lag_sum = 0.0
        self._schedule_lag_max = 0.0
        self._function_counts = Counter()
        self._function_lines: Dict[tuple, Counter] = {}
        self._variables: Dict[str, _VariableAccumulator] = {}
        self._next_transition_index = 0

    def append(self, sample: LiveSample) -> None:
        if not isinstance(sample, LiveSample):
            raise TypeError("LiveMonitorStore accepts LiveSample values only.")
        with self._lock:
            self._samples.append(sample)
            self._total_samples += 1
            if sample.overrun:
                self._overruns += 1
            self._read_duration_sum += max(0.0, float(sample.read_duration_seconds))
            self._read_duration_max = max(
                self._read_duration_max, max(0.0, float(sample.read_duration_seconds))
            )
            read_started_elapsed = max(
                0.0, float(sample.captured_elapsed_seconds) - float(sample.read_duration_seconds)
            )
            lag = max(0.0, read_started_elapsed - float(sample.scheduled_elapsed_seconds))
            self._schedule_lag_sum += lag
            self._schedule_lag_max = max(self._schedule_lag_max, lag)

            function = sample.source.function or "<unknown>"
            key = (function, sample.source.file)
            self._function_counts[key] += 1
            self._function_lines.setdefault(key, Counter())[sample.source.line] += 1
            if sample.source.function is None:
                self._unknown_source_samples += 1

            for value in sample.values:
                current = self._variables.get(value.name)
                if current is None:
                    current = _VariableAccumulator(value.value_type, value.address)
                    self._variables[value.name] = current
                elif current.value_type != value.value_type or current.address != value.address:
                    raise ValueError(
                        "Live variable identity changed during one session: %s." % value.name
                    )
                current.add(value)
                if not value.coherent:
                    self._incoherent_values += 1

            self._append_transition(sample)

    def _append_transition(self, sample: LiveSample) -> None:
        key = _source_key(sample)
        if self._transitions:
            previous = self._transitions[-1]
            previous_key = (previous.function, previous.file, previous.line)
            if previous_key == key:
                self._transitions[-1] = LiveExecutionTransition(
                    index=previous.index, start_elapsed_seconds=previous.start_elapsed_seconds,
                    end_elapsed_seconds=sample.captured_elapsed_seconds, samples=previous.samples + 1,
                    pc=sample.pc, function=sample.source.function, file=sample.source.file,
                    line=sample.source.line,
                )
                return
        transition = LiveExecutionTransition(
            index=self._next_transition_index, start_elapsed_seconds=sample.captured_elapsed_seconds,
            end_elapsed_seconds=sample.captured_elapsed_seconds, samples=1, pc=sample.pc,
            function=sample.source.function, file=sample.source.file, line=sample.source.line,
        )
        self._next_transition_index += 1
        self._transitions.append(transition)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._transitions.clear()
            self._reset_totals()

    def samples(self, limit: Optional[int] = None) -> Tuple[LiveSample, ...]:
        with self._lock:
            return _bounded_tail(tuple(self._samples), limit)

    def transitions(self, limit: Optional[int] = None) -> Tuple[LiveExecutionTransition, ...]:
        with self._lock:
            return _bounded_tail(tuple(self._transitions), limit)

    def variable_series(self, name: str, limit: Optional[int] = None) -> Tuple[LiveSeriesPoint, ...]:
        selected = str(name).strip()
        if not selected:
            raise ValueError("Live variable series name must not be empty.")
        points = []
        with self._lock:
            for sample in self._samples:
                for value in sample.values:
                    if value.name != selected:
                        continue
                    points.append(LiveSeriesPoint(
                        elapsed_seconds=sample.captured_elapsed_seconds,
                        value=_numeric(value.value) if value.coherent else None,
                        coherent=value.coherent, raw_hex=value.raw_hex,
                    ))
                    break
        return _bounded_tail(tuple(points), limit)

    def snapshot(self, top_functions: int = 20) -> LiveAnalyticsSnapshot:
        if not 1 <= int(top_functions) <= 1000:
            raise ValueError("top_functions must be in range 1..1000.")
        with self._lock:
            total = self._total_samples
            timing = LiveTimingStats(
                total_samples=total, retained_samples=len(self._samples), capacity=self.capacity,
                overruns=self._overruns, unknown_source_samples=self._unknown_source_samples,
                incoherent_values=self._incoherent_values,
                mean_read_duration_seconds=(self._read_duration_sum / total if total else 0.0),
                max_read_duration_seconds=self._read_duration_max,
                mean_schedule_lag_seconds=(self._schedule_lag_sum / total if total else 0.0),
                max_schedule_lag_seconds=self._schedule_lag_max,
            )
            ranked = sorted(
                self._function_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1] or ""),
            )[:int(top_functions)]
            functions = []
            for key, count in ranked:
                line_counts = self._function_lines.get(key, Counter())
                representative_line = None
                if line_counts:
                    representative_line = sorted(
                        line_counts.items(), key=lambda item: (-item[1], item[0] is None, item[0] or -1)
                    )[0][0]
                functions.append(LiveFunctionStat(
                    function=key[0], file=key[1], line=representative_line, samples=count,
                    share=(count / total if total else 0.0),
                ))
            functions = tuple(functions)
            variables = tuple(
                self._variables[name].snapshot(name) for name in sorted(self._variables)
            )
            latest = self._samples[-1] if self._samples else None
            return LiveAnalyticsSnapshot(timing, functions, variables, latest)

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)


def _bounded_tail(values: tuple, limit: Optional[int]):
    if limit is None:
        return values
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("History limit must be a non-negative integer or omitted.")
    if limit == 0:
        return ()
    return values[-limit:]
