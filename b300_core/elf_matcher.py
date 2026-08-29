"""Lightweight ELF/AXF-to-target matching without third-party ELF libraries."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

from .policy import APPLICATION_ADDRESS, FLASH_END_ADDRESS

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
EM_ARM = 40
PT_LOAD = 1
PF_X = 0x1
MAX_ELF_BYTES = 128 * 1024 * 1024
MAX_PROGRAM_HEADERS = 128
DEFAULT_SAMPLE_WORDS = 4
DEFAULT_SAMPLE_WINDOWS = 4

WordReader = Callable[[int, int], Sequence[int]]


@dataclass(frozen=True)
class ElfLoadSegment:
    file_offset: int
    address: int
    file_size: int
    flags: int

    @property
    def end_address(self) -> int:
        return self.address + self.file_size

    @property
    def executable(self) -> bool:
        return bool(self.flags & PF_X)


@dataclass(frozen=True)
class ElfSample:
    address: int
    data: bytes


@dataclass(frozen=True)
class SymbolMatchResult:
    path: Path
    matched: bool
    matched_samples: int
    total_samples: int
    score: float
    reason: str


def _checked_elf_path(path: Path) -> Path:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() not in (".elf", ".axf"):
        raise ValueError("Debug symbol file must end in .elf or .axf.")
    if not source.is_file():
        raise ValueError("Debug symbol file does not exist: %s" % source)
    size = source.stat().st_size
    if size < 52:
        raise ValueError("ELF/AXF file is too small to contain an ELF32 header.")
    if size > MAX_ELF_BYTES:
        raise ValueError("ELF/AXF file exceeds the supported size limit.")
    return source


def load_segments(path: Path) -> Tuple[ElfLoadSegment, ...]:
    """Parse only ELF32 little-endian ARM PT_LOAD headers required for matching."""
    source = _checked_elf_path(path)
    size = source.stat().st_size
    with source.open("rb") as stream:
        header = stream.read(52)
        if header[:4] != ELF_MAGIC:
            raise ValueError("Debug symbol file is not an ELF image.")
        if header[4] != ELFCLASS32:
            raise ValueError("Only ELF32 debug symbols are supported for STM32F407.")
        if header[5] != ELFDATA2LSB:
            raise ValueError("Only little-endian ELF debug symbols are supported.")
        machine = struct.unpack_from("<H", header, 18)[0]
        if machine != EM_ARM:
            raise ValueError("ELF machine is not ARM (EM_ARM).")
        phoff = struct.unpack_from("<I", header, 28)[0]
        phentsize = struct.unpack_from("<H", header, 42)[0]
        phnum = struct.unpack_from("<H", header, 44)[0]
        if phnum == 0:
            raise ValueError("ELF image has no program headers.")
        if phnum > MAX_PROGRAM_HEADERS:
            raise ValueError("ELF image has too many program headers.")
        if phentsize < 32:
            raise ValueError("ELF program-header entry is smaller than ELF32 requires.")
        table_end = phoff + phentsize * phnum
        if phoff < 52 or table_end > size:
            raise ValueError("ELF program-header table is outside the file.")
        segments = []
        for index in range(phnum):
            stream.seek(phoff + index * phentsize)
            entry = stream.read(32)
            if len(entry) != 32:
                raise ValueError("ELF program-header table is truncated.")
            p_type, p_offset, p_vaddr, p_paddr, p_filesz, _p_memsz, p_flags, _p_align = \
                struct.unpack("<IIIIIIII", entry)
            if p_type != PT_LOAD or p_filesz == 0:
                continue
            if p_offset + p_filesz > size:
                raise ValueError("ELF load segment extends outside the file.")
            address = p_paddr or p_vaddr
            start = max(address, APPLICATION_ADDRESS)
            end = min(address + p_filesz, FLASH_END_ADDRESS)
            if start >= end:
                continue
            delta = start - address
            segments.append(ElfLoadSegment(
                file_offset=p_offset + delta,
                address=start,
                file_size=end - start,
                flags=p_flags,
            ))
    if not segments:
        raise ValueError("ELF/AXF contains no loadable Application Flash segment.")
    return tuple(sorted(segments, key=lambda item: (not item.executable, item.address)))


def _window_offsets(length: int, window_bytes: int, limit: int) -> Tuple[int, ...]:
    if length < window_bytes:
        return ()
    maximum = length - window_bytes
    raw = (0, maximum // 3, (maximum * 2) // 3, maximum)
    result = []
    for offset in raw:
        aligned = offset - (offset % 4)
        if aligned not in result and aligned + window_bytes <= length:
            result.append(aligned)
        if len(result) >= limit:
            break
    return tuple(result)


def samples(path: Path, *, sample_words: int = DEFAULT_SAMPLE_WORDS,
            max_windows: int = DEFAULT_SAMPLE_WINDOWS) -> Tuple[ElfSample, ...]:
    if not 1 <= sample_words <= 64:
        raise ValueError("ELF sample word count must be in range 1..64.")
    if not 1 <= max_windows <= 16:
        raise ValueError("ELF sample window count must be in range 1..16.")
    source = _checked_elf_path(path)
    segments = load_segments(source)
    window_bytes = sample_words * 4
    selected = []
    # Prefer executable code; fall back to any Application PT_LOAD segment.
    ordered = tuple(segment for segment in segments if segment.executable) or segments
    with source.open("rb") as stream:
        for segment in ordered:
            for relative in _window_offsets(segment.file_size, window_bytes, max_windows):
                address = segment.address + relative
                if address % 4:
                    continue
                stream.seek(segment.file_offset + relative)
                data = stream.read(window_bytes)
                if len(data) != window_bytes:
                    continue
                # Skip filler-only samples; they carry little identity value.
                if len(set(data)) == 1 and data[0] in (0x00, 0xFF):
                    continue
                selected.append(ElfSample(address, data))
                if len(selected) >= max_windows:
                    return tuple(selected)
    if not selected:
        raise ValueError("ELF/AXF has no useful aligned Application Flash samples.")
    return tuple(selected)


def _words_to_bytes(words: Sequence[int]) -> bytes:
    output = bytearray()
    for word in words:
        if not 0 <= int(word) <= 0xFFFFFFFF:
            raise ValueError("Target word reader returned a non-32-bit value.")
        output.extend(int(word).to_bytes(4, "little"))
    return bytes(output)


def match_symbol_file(path: Path, reader: WordReader, *,
                      sample_words: int = DEFAULT_SAMPLE_WORDS,
                      max_windows: int = DEFAULT_SAMPLE_WINDOWS) -> SymbolMatchResult:
    source = _checked_elf_path(path)
    identity = samples(source, sample_words=sample_words, max_windows=max_windows)
    matched = 0
    for sample in identity:
        target = _words_to_bytes(reader(sample.address, len(sample.data) // 4))
        if target == sample.data:
            matched += 1
    total = len(identity)
    score = matched / total if total else 0.0
    exact = total > 0 and matched == total
    reason = ("All sampled Application Flash bytes match the ELF/AXF image."
              if exact else "%d of %d sampled Flash windows match." % (matched, total))
    return SymbolMatchResult(source, exact, matched, total, score, reason)


def discover_symbol_files(roots: Iterable[Path], *, max_files: int = 128,
                          max_depth: int = 8) -> Tuple[Path, ...]:
    """Bounded project-tree discovery; never performs an unbounded whole-disk scan."""
    if not 1 <= max_files <= 1024:
        raise ValueError("Symbol discovery max_files must be in range 1..1024.")
    if not 0 <= max_depth <= 16:
        raise ValueError("Symbol discovery max_depth must be in range 0..16.")
    found = []
    seen = set()
    skipped = {".git", ".svn", ".hg", ".venv", "venv", "node_modules", "__pycache__"}
    for root_value in roots:
        root = Path(root_value).expanduser().resolve()
        if root.is_file():
            candidates = (root,)
        elif root.is_dir():
            candidates = ()
            base_depth = len(root.parts)
            for current, directories, filenames in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                directories[:] = [name for name in directories if name not in skipped]
                if depth >= max_depth:
                    directories[:] = []
                for filename in filenames:
                    if Path(filename).suffix.lower() not in (".elf", ".axf"):
                        continue
                    candidate = current_path / filename
                    key = str(candidate).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(candidate)
                    if len(found) >= max_files:
                        return tuple(sorted(found, key=lambda item: str(item).lower()))
            continue
        else:
            continue
        for candidate in candidates:
            if candidate.suffix.lower() in (".elf", ".axf"):
                key = str(candidate).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(candidate)
                    if len(found) >= max_files:
                        return tuple(sorted(found, key=lambda item: str(item).lower()))
    return tuple(sorted(found, key=lambda item: str(item).lower()))


def find_matching_symbol_file(candidates: Iterable[Path], reader: WordReader, *,
                              sample_words: int = DEFAULT_SAMPLE_WORDS,
                              max_windows: int = DEFAULT_SAMPLE_WINDOWS) -> Tuple[Optional[SymbolMatchResult], Tuple[SymbolMatchResult, ...]]:
    results = []
    for candidate in candidates:
        try:
            result = match_symbol_file(
                candidate, reader, sample_words=sample_words, max_windows=max_windows,
            )
        except (OSError, ValueError):
            continue
        results.append(result)
    exact = [result for result in results if result.matched]
    if len(exact) == 1:
        return exact[0], tuple(results)
    # Ambiguous exact matches fail closed: caller must choose explicitly.
    return None, tuple(results)
