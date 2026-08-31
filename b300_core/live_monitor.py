"""Zero-halt DWT/RAM live monitoring for a running STM32F407 target."""

from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple, Union

from .offline_symbols import OfflineSymbolTable, SourceLocation
from .tcl_client import SafeTclClient


DWT_PCSR_ADDRESS = 0xE000101C
MIN_LIVE_INTERVAL_SECONDS = 0.1
MAX_LIVE_INTERVAL_SECONDS = 60.0
MAX_LIVE_WATCHES = 16
MAX_LIVE_SAMPLES = 100000
MAX_LIVE_READ_WORDS = 32
F407_RAM_RANGES = ((0x10000000, 0x10010000), (0x20000000, 0x20020000))

_TYPE_FORMATS = {
    "u8": (1, "<B"), "i8": (1, "<b"),
    "u16": (2, "<H"), "i16": (2, "<h"),
    "u32": (4, "<I"), "i32": (4, "<i"),
    "f32": (4, "<f"), "f64": (8, "<d"),
}


@dataclass(frozen=True)
class LiveWatch:
    name: str
    value_type: str
    address: int
    size: int


@dataclass(frozen=True)
class LiveValue:
    name: str
    value_type: str
    address: int
    value: object
    raw_hex: str
    coherent: bool = True
    verification_raw_hex: Optional[str] = None


@dataclass(frozen=True)
class LiveSample:
    cycle: int
    scheduled_elapsed_seconds: float
    captured_elapsed_seconds: float
    read_duration_seconds: float
    overrun: bool
    pc: int
    source: SourceLocation
    values: Tuple[LiveValue, ...]

    def to_record(self) -> dict:
        return {
            "cycle": self.cycle,
            "scheduled_elapsed_seconds": round(self.scheduled_elapsed_seconds, 6),
            "captured_elapsed_seconds": round(self.captured_elapsed_seconds, 6),
            "read_duration_seconds": round(self.read_duration_seconds, 6),
            "overrun": self.overrun,
            "pc": "0x%08X" % self.pc,
            "function": self.source.function,
            "file": self.source.file,
            "line": self.source.line,
            "values": [
                {
                    "name": item.name, "type": item.value_type,
                    "address": "0x%08X" % item.address,
                    "value": item.value, "raw_hex": item.raw_hex,
                    "coherent": item.coherent,
                    "verification_raw_hex": item.verification_raw_hex,
                }
                for item in self.values
            ],
        }


@dataclass(frozen=True)
class LiveSummary:
    samples: int
    interval_seconds: float
    elapsed_seconds: float
    overruns: int
    cancelled: bool
    final_target_state: str

    def to_record(self) -> dict:
        return {
            "samples": self.samples,
            "interval_seconds": self.interval_seconds,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "overruns": self.overruns,
            "cancelled": self.cancelled,
            "final_target_state": self.final_target_state,
        }


def validate_live_watch_specs(specs: Iterable[str]) -> Tuple[Tuple[str, str], ...]:
    selected = []
    for spec in specs:
        text = str(spec).strip()
        if not text or text.count(":") != 1:
            raise ValueError("Live watch must use NAME:TYPE, for example xTickCount:u32.")
        name, value_type = (part.strip() for part in text.split(":", 1))
        if not name:
            raise ValueError("Live watch symbol name must not be empty.")
        if value_type not in _TYPE_FORMATS:
            raise ValueError("Unsupported live watch type %s; use %s." %
                             (value_type, ",".join(sorted(_TYPE_FORMATS))))
        if any(existing_name == name for existing_name, _existing_type in selected):
            raise ValueError("Live watch symbol is duplicated: %s" % name)
        selected.append((name, value_type))
    if len(selected) > MAX_LIVE_WATCHES:
        raise ValueError("At most %d live watches are allowed." % MAX_LIVE_WATCHES)
    return tuple(selected)


def parse_live_watch(spec: str, symbols: OfflineSymbolTable) -> LiveWatch:
    ((name, value_type),) = validate_live_watch_specs((spec,))
    symbol = symbols.symbol(name)
    size, _fmt = _TYPE_FORMATS[value_type]
    end = symbol.address + size
    if not any(start <= symbol.address and end <= limit for start, limit in F407_RAM_RANGES):
        raise ValueError("Live watch symbol is not fully inside STM32F407 CCM/SRAM: %s" % name)
    if symbol.size and symbol.size < size:
        raise ValueError("Live watch type %s exceeds ELF symbol size for %s." % (value_type, name))
    return LiveWatch(name, value_type, symbol.address, size)


def validate_live_request(interval_seconds: float, samples: Optional[int], watches: Sequence[LiveWatch]) -> None:
    if not isinstance(interval_seconds, (int, float)) or isinstance(interval_seconds, bool):
        raise ValueError("Live interval must be numeric.")
    interval = float(interval_seconds)
    if not math.isfinite(interval) or not MIN_LIVE_INTERVAL_SECONDS <= interval <= MAX_LIVE_INTERVAL_SECONDS:
        raise ValueError("Live interval must be in range 0.1..60.0 seconds.")
    if samples is not None and (not isinstance(samples, int) or isinstance(samples, bool)
                                or not 1 <= samples <= MAX_LIVE_SAMPLES):
        raise ValueError("Live samples must be 1..%d or omitted for continuous mode." % MAX_LIVE_SAMPLES)
    if len(watches) > MAX_LIVE_WATCHES:
        raise ValueError("At most %d live watches are allowed." % MAX_LIVE_WATCHES)


def _word_addresses(watches: Sequence[LiveWatch]) -> Tuple[int, ...]:
    addresses = [DWT_PCSR_ADDRESS]
    for watch in watches:
        first = watch.address & ~3
        last = (watch.address + watch.size - 1) & ~3
        address = first
        while address <= last:
            if address not in addresses:
                addresses.append(address)
            address += 4
    if len(addresses) > MAX_LIVE_READ_WORDS:
        raise ValueError("Live monitor needs more than %d SWD words in one cycle." % MAX_LIVE_READ_WORDS)
    return tuple(addresses)


def _watch_raw_bytes(watch: LiveWatch, words_by_address: dict) -> bytes:
    first = watch.address & ~3
    last = (watch.address + watch.size - 1) & ~3
    raw = bytearray()
    address = first
    while address <= last:
        raw.extend(struct.pack("<I", words_by_address[address]))
        address += 4
    offset = watch.address - first
    return bytes(raw[offset:offset + watch.size])


def _decode_watch(watch: LiveWatch, words_by_address: dict,
                  verification_words_by_address: Optional[dict] = None) -> LiveValue:
    selected = _watch_raw_bytes(watch, words_by_address)
    verification = None
    coherent = True
    if watch.size > 4 and verification_words_by_address is not None:
        verification = _watch_raw_bytes(watch, verification_words_by_address)
        coherent = selected == verification
    _size, fmt = _TYPE_FORMATS[watch.value_type]
    value = struct.unpack(fmt, selected)[0] if coherent else None
    return LiveValue(
        watch.name, watch.value_type, watch.address, value, selected.hex().upper(),
        coherent=coherent,
        verification_raw_hex=verification.hex().upper() if verification is not None else None,
    )


def run_live_monitor(
    tcl: SafeTclClient,
    symbols: OfflineSymbolTable,
    *, interval_seconds: float = 0.5,
    sample_limit: Optional[int] = None,
    watch_specs: Iterable[str] = (),
    cancelled: Callable[[], bool] = lambda: False,
    wait: Callable[[float], bool] = lambda seconds: (time.sleep(seconds) or False),
    clock: Callable[[], float] = time.monotonic,
    on_sample: Optional[Callable[[LiveSample], None]] = None,
    state_check_every: int = 10,
) -> LiveSummary:
    normalized_specs = validate_live_watch_specs(watch_specs)
    watches = tuple(
        parse_live_watch("%s:%s" % (name, value_type), symbols)
        for name, value_type in normalized_specs
    )
    validate_live_request(interval_seconds, sample_limit, watches)
    if tcl.wait_target_state() != "running":
        raise RuntimeError("Realtime Live Monitor requires a RUNNING target and will not resume it automatically.")
    addresses = _word_addresses(watches)
    coherence_addresses = []
    for watch in watches:
        if watch.size <= 4:
            continue
        address = watch.address & ~3
        last = (watch.address + watch.size - 1) & ~3
        while address <= last:
            if address not in coherence_addresses:
                coherence_addresses.append(address)
            address += 4
    request_addresses = addresses + tuple(coherence_addresses)
    if len(request_addresses) > MAX_LIVE_READ_WORDS:
        raise ValueError(
            "Live monitor needs %d SWD word reads including 64-bit coherence checks; max is %d." %
            (len(request_addresses), MAX_LIVE_READ_WORDS)
        )
    start = clock()
    cycle = 0
    overruns = 0
    was_cancelled = False
    try:
        while sample_limit is None or cycle < sample_limit:
            if cancelled():
                was_cancelled = True
                break
            scheduled = cycle * float(interval_seconds)
            remaining = start + scheduled - clock()
            if remaining > 0 and wait(remaining):
                was_cancelled = True
                break
            if cancelled():
                was_cancelled = True
                break
            read_started = clock()
            words = tcl.read_word_addresses(request_addresses)
            read_finished = clock()
            base_count = len(addresses)
            mapping = dict(zip(addresses, words[:base_count]))
            verification_mapping = dict(zip(coherence_addresses, words[base_count:]))
            pc = mapping[DWT_PCSR_ADDRESS] & ~1
            source = symbols.source_location(pc)
            values = tuple(
                _decode_watch(watch, mapping, verification_mapping if watch.size > 4 else None)
                for watch in watches
            )
            duration = read_finished - read_started
            overrun = duration > float(interval_seconds)
            if overrun:
                overruns += 1
            sample = LiveSample(
                cycle=cycle, scheduled_elapsed_seconds=scheduled,
                captured_elapsed_seconds=read_finished - start,
                read_duration_seconds=duration, overrun=overrun,
                pc=pc, source=source, values=values,
            )
            if on_sample is not None:
                on_sample(sample)
            cycle += 1
            if state_check_every > 0 and cycle % state_check_every == 0:
                state = tcl.wait_target_state()
                if state != "running":
                    raise RuntimeError("Realtime target stopped unexpectedly: %s" % state)
    finally:
        final_state = tcl.wait_target_state()
    if final_state != "running":
        raise RuntimeError("Realtime Live Monitor ended with target state %s; refusing to hide it." % final_state)
    return LiveSummary(cycle, float(interval_seconds), clock() - start, overruns, was_cancelled, final_state)


def save_watch_preset(
    path: Union[str, Path],
    specs: Iterable[str],
    *,
    name: Optional[str] = None,
    interval_seconds: Optional[float] = None,
    sample_limit: Optional[int] = None,
    plot_flags: Optional[Dict[str, bool]] = None,
) -> Path:
    validated_specs = validate_live_watch_specs(specs)
    if interval_seconds is not None:
        interval = float(interval_seconds)
        if not MIN_LIVE_INTERVAL_SECONDS <= interval <= MAX_LIVE_INTERVAL_SECONDS:
            raise ValueError("Live interval must be in range 0.1..60.0 seconds.")
    if sample_limit is not None:
        if not 1 <= int(sample_limit) <= MAX_LIVE_SAMPLES:
            raise ValueError("Live sample limit must be in range 1..%d." % MAX_LIVE_SAMPLES)
    plot_map = dict(plot_flags or {})
    watches = []
    for var_name, var_type in validated_specs:
        watches.append({
            "name": var_name,
            "type": var_type,
            "plot": bool(plot_map.get(var_name, True)),
        })
    data = {
        "schema_version": 1,
        "name": str(name or "B300 Live Watch Preset").strip(),
        "interval_seconds": float(interval_seconds) if interval_seconds is not None else None,
        "sample_limit": int(sample_limit) if sample_limit is not None else None,
        "watches": watches,
    }
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target_path


def load_watch_preset(path: Union[str, Path]) -> dict:
    target_path = Path(path)
    if not target_path.is_file():
        raise ValueError("Watch preset file not found: %s" % target_path)
    try:
        content = target_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        raise ValueError("Invalid JSON in watch preset: %s" % exc) from exc

    specs = []
    plot_flags = {}
    name = "B300 Live Watch Preset"
    interval_seconds = None
    sample_limit = None

    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        name = str(data.get("name") or name).strip()
        raw_interval = data.get("interval_seconds")
        if raw_interval is not None:
            interval_seconds = float(raw_interval)
            if not MIN_LIVE_INTERVAL_SECONDS <= interval_seconds <= MAX_LIVE_INTERVAL_SECONDS:
                raise ValueError("Live interval in preset must be in range 0.1..60.0 seconds.")
        raw_limit = data.get("sample_limit")
        if raw_limit is not None:
            sample_limit = int(raw_limit)
            if not 1 <= sample_limit <= MAX_LIVE_SAMPLES:
                raise ValueError("Live sample limit in preset must be in range 1..%d." % MAX_LIVE_SAMPLES)
        raw_items = data.get("watches") or data.get("variables") or []
        if not isinstance(raw_items, list):
            raise ValueError("Watch preset must contain a list of watches.")
    else:
        raise ValueError("Watch preset root must be a JSON object or list.")

    for item in raw_items:
        if isinstance(item, str):
            spec_str = item.strip()
            if not spec_str:
                continue
            specs.append(spec_str)
            var_name = spec_str.split(":", 1)[0].strip() if ":" in spec_str else spec_str
            plot_flags[var_name] = True
        elif isinstance(item, dict):
            var_name = str(item.get("name") or "").strip()
            var_type = str(item.get("type") or "").strip()
            if not var_name or not var_type:
                raise ValueError("Each watch item must have 'name' and 'type'.")
            specs.append("%s:%s" % (var_name, var_type))
            plot_flags[var_name] = bool(item.get("plot", True))
        else:
            raise ValueError("Unsupported watch item in preset: %r" % item)

    validated = validate_live_watch_specs(specs)
    normalized_specs = tuple("%s:%s" % (n, t) for n, t in validated)
    watches_list = [
        {"name": n, "type": t, "plot": plot_flags.get(n, True)}
        for n, t in validated
    ]

    return {
        "schema_version": 1,
        "name": name,
        "interval_seconds": interval_seconds,
        "sample_limit": sample_limit,
        "specs": normalized_specs,
        "plot_flags": {n: plot_flags.get(n, True) for n, _ in validated},
        "watches": watches_list,
    }

