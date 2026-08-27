from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"
VALID_HEX = ":020000040801F1\n:0100000000FF\n:00000001FF\n"
BOOTLOADER_HEX = ":020000040800F2\n:0100000000FF\n:00000001FF\n"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B300StlinkTests(unittest.TestCase):
    def test_flash_erases_only_metadata_and_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 0)
        command = next(json.loads(line)["command"] for line in output.getvalue().splitlines()
                       if json.loads(line)["event"] == "openocd")
        rendered = " ".join(command)
        self.assertIn("flash erase_sector 0 3 7", rendered)
        self.assertIn("program", rendered)
        self.assertIn("verify", rendered)
        self.assertIn("mww 0x40002860 0x53544C4B", rendered)
        self.assertNotIn("mass_erase", rendered)

    def test_flash_rejects_bootloader_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad.hex"
            image.write_text(BOOTLOADER_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("protected range", output.getvalue())

    def test_debug_has_no_flash_write_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        rendered = output.getvalue().lower()
        self.assertNotIn("erase_sector", rendered)
        self.assertNotIn("program {", rendered)
        self.assertNotIn("mww ", rendered)


if __name__ == "__main__":
    unittest.main()
