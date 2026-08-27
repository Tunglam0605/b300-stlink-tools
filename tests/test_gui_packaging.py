from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

import package_internal


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "build_gui.py"


def gui_builder():
    spec = importlib.util.spec_from_file_location("build_gui", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuiPackagingTests(unittest.TestCase):
    def test_internal_zip_contains_cli_gui_openocd_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "b300-stlink.exe"
            gui = root / "b300-stlink-gui.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            (openocd / "bin").mkdir(parents=True)
            for path in (cli, gui, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"test")
            output = root / "bundle.zip"
            openocd_sha256 = "A" * 64
            result = package_internal.main([
                "--executable", str(cli),
                "--gui-executable", str(gui),
                "--openocd-root", str(openocd),
                "--bootstrap", str(bootstrap),
                "--output", str(output),
                "--platform", "windows-x64",
                "--openocd-archive", "xpack-openocd-0.12.0-7-win32-x64.zip",
                "--openocd-sha256", openocd_sha256,
                "--internal-distribution-approved",
            ])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                metadata = archive.read("BUNDLE-METADATA.txt").decode("ascii")
        self.assertEqual(result, 0)
        self.assertIn("b300-stlink.exe", names)
        self.assertIn("b300-stlink-gui.exe", names)
        self.assertIn("vendor/openocd/bin/openocd.exe", names)
        self.assertIn("BUNDLE-METADATA.txt", names)
        self.assertIn(
            "openocd_archive=xpack-openocd-0.12.0-7-win32-x64.zip", metadata
        )
        self.assertIn("openocd_sha256=%s" % openocd_sha256, metadata)

    def test_linux_staging_contains_launchers_desktop_icon_and_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            (bundle / "vendor" / "openocd" / "bin").mkdir(parents=True)
            for relative in (
                "b300-stlink", "b300-stlink-gui", "vendor/openocd/bin/openocd"
            ):
                path = bundle / relative
                path.write_bytes(b"binary")
            output = root / "output"
            appdir = gui_builder().stage_linux_appdir(bundle, output, "x86_64")
            debroot = gui_builder().stage_deb_root(bundle, output, "amd64", "0.1.0")

            self.assertTrue((appdir / "AppRun").is_file())
            self.assertTrue((appdir / "b300-stlink-gui.desktop").is_file())
            self.assertTrue((appdir / "b300-stlink-gui.svg").is_file())
            self.assertTrue((appdir / "usr" / "bin" / "b300-stlink-gui").is_file())
            self.assertTrue((debroot / "DEBIAN" / "control").is_file())
            self.assertTrue((debroot / "usr" / "local" / "bin" /
                             "b300-stlink-gui").is_file())


if __name__ == "__main__":
    unittest.main()
