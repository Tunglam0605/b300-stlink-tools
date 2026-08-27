from __future__ import annotations

import importlib.util
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import package_internal
import build_native_bundle


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "build_gui.py"


def gui_builder():
    spec = importlib.util.spec_from_file_location("build_gui", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuiPackagingTests(unittest.TestCase):
    def test_gui_output_names_are_stable_and_platform_specific(self) -> None:
        module = gui_builder()
        self.assertEqual(
            module.gui_output_names("x86_64"),
            ("B300-STLink-GUI-Ubuntu-x64.AppImage", "b300-stlink-gui_amd64.deb"),
        )
        self.assertEqual(
            module.gui_output_names("aarch64"),
            ("B300-STLink-GUI-Ubuntu-arm64.AppImage", "b300-stlink-gui_arm64.deb"),
        )

    def test_linux_x64_release_uses_ubuntu_2204_compatibility_baseline(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        x64_entry = workflow.split("- architecture: x86_64", 1)[1].split(
            "- architecture: aarch64", 1
        )[0]
        self.assertIn("runner: ubuntu-22.04", x64_entry)

    def test_brand_assets_are_wired_into_native_executables(self) -> None:
        self.assertTrue((ROOT / "branding" / "logo.png").is_file())
        self.assertTrue((ROOT / "branding" / "b300-stlink-icon.png").is_file())
        self.assertTrue((ROOT / "branding" / "b300-stlink-icon.ico").is_file())
        self.assertTrue((ROOT / "branding" / "b300-stlink-wordmark.png").is_file())
        self.assertTrue(
            (ROOT / "packaging" / "linux" / "b300-stlink-gui.svg").is_file()
        )
        gui_spec = (ROOT / "b300_gui.spec").read_text(encoding="utf-8")
        native_builder = (ROOT / "build_native_bundle.py").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "windows" /
                     "b300-stlink-gui.iss").read_text(encoding="utf-8")
        self.assertIn('icon=str(project_root / "branding" / "b300-stlink-icon.ico")',
                      gui_spec)
        self.assertIn('"--icon", str(ROOT / "branding" / "b300-stlink-icon.ico")',
                      native_builder)
        linux_resources = {path.name for path in build_native_bundle.gui_resources("linux-x64")}
        windows_resources = {path.name for path in build_native_bundle.gui_resources("windows-x64")}
        self.assertIn("b300-stlink-gui.svg", linux_resources)
        self.assertNotIn("b300-stlink-gui.svg", windows_resources)
        self.assertGreaterEqual(native_builder.count('"--clean"'), 2)
        self.assertGreaterEqual(native_builder.count('"--workpath"'), 2)
        self.assertNotIn('ROOT / "build"', native_builder)
        self.assertIn("SetupIconFile={#SourceRoot}\\b300-stlink-icon.ico", installer)

    def test_linux_staging_uses_python_39_compatible_text_writes(self) -> None:
        original_write_text = Path.write_text

        def python39_write_text(path, data, encoding=None, errors=None):
            return original_write_text(path, data, encoding=encoding, errors=errors)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            (bundle / "vendor" / "openocd" / "bin").mkdir(parents=True)
            for relative in (
                "b300-stlink", "b300-stlink-gui", "vendor/openocd/bin/openocd"
            ):
                (bundle / relative).write_bytes(b"binary")

            with mock.patch.object(Path, "write_text", python39_write_text):
                gui_builder().stage_linux_appdir(bundle, root / "output", "x86_64")
                gui_builder().stage_deb_root(
                    bundle, root / "output", "amd64", "0.1.0"
                )

    def test_internal_gui_zip_excludes_cli_and_contains_openocd_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "b300-stlink.exe"
            gui = root / "b300-stlink-gui.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack-openocd.zip"
            (openocd / "bin").mkdir(parents=True)
            for path in (cli, gui, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"test")
            xpack.write_bytes(b"trusted archive")
            unicode_resource = openocd / "share" / "tài-liệu.txt"
            unicode_resource.parent.mkdir(parents=True)
            unicode_resource.write_bytes(b"offline docs")
            output = root / "bundle.zip"
            openocd_sha256 = "A" * 64
            manifest_digest = hashlib.sha256(
                package_internal.openocd_manifest(openocd)
            ).hexdigest()
            with mock.patch.object(
                package_internal,
                "TRUSTED_TREE_MANIFESTS",
                {"windows-x64": manifest_digest},
            ):
                result = package_internal.main([
                    "--flavor", "gui",
                    "--executable", str(gui),
                    "--openocd-root", str(openocd),
                    "--bootstrap", str(bootstrap),
                    "--output", str(output),
                    "--platform", "windows-x64",
                    "--openocd-archive", "xpack-openocd-0.12.0-7-win32-x64.zip",
                    "--openocd-sha256", openocd_sha256,
                    "--openocd-package", str(xpack),
                    "--internal-distribution-approved",
                ])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                metadata = archive.read("BUNDLE-METADATA.txt").decode("ascii")
                manifest = archive.read(
                    "vendor/openocd/OPENOCD-MANIFEST.sha256"
                ).decode("utf-8")
        self.assertEqual(result, 0)
        self.assertIn("b300-stlink-gui.exe", names)
        self.assertNotIn("b300-stlink.exe", names)
        self.assertIn("vendor/openocd/bin/openocd.exe", names)
        self.assertIn("BUNDLE-METADATA.txt", names)
        self.assertIn("vendor/openocd/OPENOCD-MANIFEST.sha256", names)
        self.assertIn(
            "vendor/packages/xpack-openocd-0.12.0-7-win32-x64.zip", names
        )
        self.assertIn("vendor/openocd/share/tài-liệu.txt", names)
        self.assertIn("vendor/openocd/share/tài-liệu.txt", manifest)
        self.assertIn(
            "openocd_archive=xpack-openocd-0.12.0-7-win32-x64.zip", metadata
        )
        self.assertIn("openocd_sha256=%s" % openocd_sha256, metadata)
        self.assertIn("flavor=gui", metadata)

    def test_internal_cli_zip_excludes_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "b300-stlink.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack-openocd.zip"
            (openocd / "bin").mkdir(parents=True)
            for path in (cli, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"test")
            xpack.write_bytes(b"trusted archive")
            output = root / "cli.zip"
            manifest_digest = hashlib.sha256(
                package_internal.openocd_manifest(openocd)
            ).hexdigest()
            with mock.patch.object(
                package_internal,
                "TRUSTED_TREE_MANIFESTS",
                {"windows-x64": manifest_digest},
            ):
                package_internal.main([
                    "--flavor", "cli",
                    "--executable", str(cli),
                    "--openocd-root", str(openocd),
                    "--bootstrap", str(bootstrap),
                    "--output", str(output),
                    "--platform", "windows-x64",
                    "--openocd-archive", "xpack-openocd-0.12.0-7-win32-x64.zip",
                    "--openocd-sha256", "A" * 64,
                    "--openocd-package", str(xpack),
                    "--internal-distribution-approved",
                ])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                metadata = archive.read("BUNDLE-METADATA.txt").decode("ascii")
        self.assertIn("b300-stlink.exe", names)
        self.assertNotIn("b300-stlink-gui.exe", names)
        self.assertIn("flavor=cli", metadata)

    def test_linux_staging_contains_launchers_desktop_icon_and_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            (bundle / "vendor" / "openocd" / "bin").mkdir(parents=True)
            for relative in ("b300-stlink-gui", "vendor/openocd/bin/openocd"):
                path = bundle / relative
                path.write_bytes(b"binary")
            output = root / "output"
            appdir = gui_builder().stage_linux_appdir(bundle, output, "x86_64")
            debroot = gui_builder().stage_deb_root(bundle, output, "amd64", "0.1.0")

            self.assertTrue((appdir / "AppRun").is_file())
            self.assertTrue((appdir / "b300-stlink-gui.desktop").is_file())
            self.assertTrue((appdir / "b300-stlink-gui.png").is_file())
            self.assertTrue((appdir / "usr" / "bin" / "b300-stlink-gui").is_file())
            self.assertFalse((appdir / "usr" / "bin" / "b300-stlink").exists())
            self.assertTrue((debroot / "DEBIAN" / "control").is_file())
            self.assertTrue((debroot / "usr" / "share" / "icons" / "hicolor" /
                             "512x512" / "apps" / "b300-stlink-gui.png").is_file())
            self.assertTrue((debroot / "usr" / "local" / "bin" /
                             "b300-stlink-gui").is_file())
            self.assertFalse((debroot / "usr" / "local" / "bin" /
                              "b300-stlink").exists())


if __name__ == "__main__":
    unittest.main()
