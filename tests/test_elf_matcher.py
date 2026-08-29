from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from b300_core.elf_matcher import (
    discover_symbol_files, find_matching_symbol_file, load_segments,
    match_symbol_file, samples,
)


def make_elf(path: Path, payload: bytes, address: int = 0x08010000,
             *, machine: int = 40, flags: int = 5) -> None:
    phoff = 52
    phentsize = 32
    data_offset = phoff + phentsize
    header = bytearray(52)
    header[:4] = b"\x7fELF"
    header[4] = 1
    header[5] = 1
    header[6] = 1
    struct.pack_into("<H", header, 16, 2)
    struct.pack_into("<H", header, 18, machine)
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<I", header, 24, address)
    struct.pack_into("<I", header, 28, phoff)
    struct.pack_into("<H", header, 40, 52)
    struct.pack_into("<H", header, 42, phentsize)
    struct.pack_into("<H", header, 44, 1)
    program = struct.pack(
        "<IIIIIIII", 1, data_offset, address, address,
        len(payload), len(payload), flags, 4,
    )
    path.write_bytes(bytes(header) + program + payload)


class ElfMatcherTests(unittest.TestCase):
    def test_parses_arm_elf32_application_load_segment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.axf"
            make_elf(path, bytes(range(64)))
            segments = load_segments(path)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].address, 0x08010000)
        self.assertEqual(segments[0].file_size, 64)
        self.assertTrue(segments[0].executable)

    def test_rejects_non_arm_elf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.elf"
            make_elf(path, bytes(range(64)), machine=62)
            with self.assertRaisesRegex(ValueError, "not ARM"):
                load_segments(path)

    def test_matching_compares_little_endian_target_words(self) -> None:
        payload = bytes(range(64))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.axf"
            make_elf(path, payload)

            def reader(address: int, count: int):
                offset = address - 0x08010000
                data = payload[offset:offset + count * 4]
                return tuple(int.from_bytes(data[i:i + 4], "little")
                             for i in range(0, len(data), 4))

            result = match_symbol_file(path, reader)
        self.assertTrue(result.matched)
        self.assertEqual(result.score, 1.0)
        self.assertGreaterEqual(result.total_samples, 1)

    def test_one_changed_flash_window_rejects_candidate(self) -> None:
        payload = bytes(range(64))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.axf"
            make_elf(path, payload)

            def reader(address: int, count: int):
                offset = address - 0x08010000
                data = bytearray(payload[offset:offset + count * 4])
                if address == 0x08010000:
                    data[0] ^= 0xFF
                return tuple(int.from_bytes(data[i:i + 4], "little")
                             for i in range(0, len(data), 4))

            result = match_symbol_file(path, reader)
        self.assertFalse(result.matched)
        self.assertLess(result.score, 1.0)

    def test_discovery_is_bounded_and_skips_git_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "Project" / "Objects" / "main.axf"
            hidden = root / ".git" / "bad.axf"
            good.parent.mkdir(parents=True)
            hidden.parent.mkdir(parents=True)
            make_elf(good, bytes(range(64)))
            make_elf(hidden, bytes(range(64)))
            found = discover_symbol_files([root], max_files=8, max_depth=8)
            self.assertEqual(found, (good.resolve(),))

    def test_ambiguous_exact_matches_fail_closed(self) -> None:
        payload = bytes(range(64))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.axf"
            second = root / "b.axf"
            make_elf(first, payload)
            make_elf(second, payload)

            def reader(address: int, count: int):
                offset = address - 0x08010000
                data = payload[offset:offset + count * 4]
                return tuple(int.from_bytes(data[i:i + 4], "little")
                             for i in range(0, len(data), 4))

            selected, results = find_matching_symbol_file([first, second], reader)
        self.assertIsNone(selected)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.matched for item in results))

    def test_samples_skip_uniform_filler(self) -> None:
        payload = b"\xFF" * 16 + bytes(range(16, 64))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.axf"
            make_elf(path, payload)
            identity = samples(path)
        self.assertTrue(identity)
        self.assertNotEqual(identity[0].data, b"\xFF" * len(identity[0].data))


if __name__ == "__main__":
    unittest.main()
