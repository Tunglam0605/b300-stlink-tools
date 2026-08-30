from __future__ import annotations

import importlib.util
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import package_internal
import build_native_bundle


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "build_gui.py"


def _windows_tool(name: str, fixed_path: str):
    selected = shutil.which(name)
    if selected:
        return selected
    fixed = Path(fixed_path)
    return str(fixed) if fixed.is_file() else None


def _create_windows_junction(link: Path, target: Path, powershell: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    environ = os.environ.copy()
    environ["B300_TEST_LINK"] = str(link)
    environ["B300_TEST_TARGET"] = str(target)
    result = subprocess.run(
        [
            powershell, "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; "
            "New-Item -ItemType Junction -Path $env:B300_TEST_LINK "
            "-Target $env:B300_TEST_TARGET | Out-Null",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environ,
    )
    if result.returncode != 0:
        raise unittest.SkipTest("Windows junctions unavailable: %s" % result.stderr)


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

    def test_appimagetool_transient_failure_retries_and_cleans_partial_output(self) -> None:
        module = gui_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            appimage = root / "B300.AppImage"
            calls = []

            def fake_check_call(command, env=None):
                calls.append((command, env))
                if len(calls) == 1:
                    appimage.write_bytes(b"partial")
                    raise subprocess.CalledProcessError(1, command)
                self.assertFalse(appimage.exists())
                appimage.write_bytes(b"complete")
                return 0

            with mock.patch.object(module.subprocess, "check_call", side_effect=fake_check_call), \
                    mock.patch.object(module.time, "sleep") as sleep:
                module.run_appimagetool(
                    Path("appimagetool"), root / "AppDir", appimage, {"ARCH": "x86_64"},
                    attempts=3, retry_delay=0.25,
                )

            self.assertEqual(len(calls), 2)
            sleep.assert_called_once_with(0.25)
            self.assertEqual(appimage.read_bytes(), b"complete")

    def test_appimagetool_retry_exhaustion_raises_and_removes_partial_output(self) -> None:
        module = gui_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            appimage = root / "B300.AppImage"

            def always_fail(command, env=None):
                appimage.write_bytes(b"partial")
                raise subprocess.CalledProcessError(1, command)

            with mock.patch.object(module.subprocess, "check_call", side_effect=always_fail) as call, \
                    mock.patch.object(module.time, "sleep") as sleep:
                with self.assertRaises(subprocess.CalledProcessError):
                    module.run_appimagetool(
                        Path("appimagetool"), root / "AppDir", appimage, {},
                        attempts=3, retry_delay=0.5,
                    )

            self.assertEqual(call.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertFalse(appimage.exists())

    def test_arm64_build_uses_an_available_pyside_release(self) -> None:
        requirements = (ROOT / "requirements-gui.txt").read_text(encoding="utf-8")
        self.assertIn("PySide6==6.10.3; platform_machine != 'aarch64'", requirements)
        self.assertIn("PySide6==6.8.0.2; platform_machine == 'aarch64'", requirements)

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
        self.assertIn('project_root / "CHANGELOG.md"', gui_spec)
        self.assertIn('"BUILD-COMMIT.txt"', gui_spec)
        self.assertIn("load_trusted_bootloader", gui_spec)
        self.assertNotIn("v00050001.hex", gui_spec)
        self.assertNotIn("Path(WORKPATH)", gui_spec)
        self.assertIn("from b300_core.build_info import build_commit", gui_spec)
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

    def test_windows_gui_release_uses_onedir_runtime(self) -> None:
        windows_spec = (ROOT / "b300_gui_windows.spec").read_text(encoding="utf-8")
        native_builder = (ROOT / "build_native_bundle.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("exclude_binaries=True", windows_spec)
        self.assertIn("COLLECT(", windows_spec)
        self.assertIn("load_trusted_bootloader", windows_spec)
        self.assertNotIn("v00050001.hex", windows_spec)
        self.assertIn('ROOT / "b300_gui_windows.spec"', native_builder)
        self.assertIn('"--application-root"', native_builder)
        self.assertIn("Verify packaged Windows onedir runtime", workflow)
        self.assertIn("Smoke-test installed Windows GUI", workflow)
        self.assertIn("VCRUNTIME140*.dll", workflow)

    def test_application_root_packaging_preserves_windows_onedir_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_root = root / "app"
            internal = app_root / "_internal"
            internal.mkdir(parents=True)
            gui = app_root / "b300-stlink-gui.exe"
            python_dll = internal / "python39.dll"
            runtime_dll = internal / "VCRUNTIME140.dll"
            gui.write_bytes(b"gui")
            python_dll.write_bytes(b"python")
            runtime_dll.write_bytes(b"runtime")
            bootstrap = root / "install.ps1"
            bootstrap.write_bytes(b"bootstrap")
            openocd = root / "openocd"
            gdb = root / "gdb"
            (openocd / "bin").mkdir(parents=True)
            (openocd / "bin" / "openocd.exe").write_bytes(b"openocd")
            (gdb / "bin").mkdir(parents=True)
            (gdb / "bin" / "arm-none-eabi-gdb.exe").write_bytes(b"gdb")
            xpack = root / "xpack-openocd.zip"
            xpack.write_bytes(b"trusted")
            output = root / "bundle.zip"
            manifest_digest = hashlib.sha256(
                package_internal.openocd_manifest(openocd)
            ).hexdigest()
            with mock.patch.object(
                package_internal, "TRUSTED_TREE_MANIFESTS",
                {"windows-x64": manifest_digest},
            ):
                package_internal.main([
                    "--flavor", "gui",
                    "--executable", str(gui),
                    "--application-root", str(app_root),
                    "--openocd-root", str(openocd),
                    "--gdb-root", str(gdb), "--gdb-archive", "xpack-gdb.zip",
                    "--gdb-sha256", "B" * 64,
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
        self.assertIn("b300-stlink-gui.exe", names)
        self.assertIn("_internal/python39.dll", names)
        self.assertIn("_internal/VCRUNTIME140.dll", names)

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
            gdb = root / "gdb"
            xpack = root / "xpack-openocd.zip"
            (openocd / "bin").mkdir(parents=True)
            (gdb / "bin").mkdir(parents=True)
            for path in (cli, gui, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"test")
            (gdb / "bin" / "arm-none-eabi-gdb.exe").write_bytes(b"gdb")
            xpack.write_bytes(b"trusted archive")
            firmware = root / "resources" / "firmware"
            firmware.mkdir(parents=True)
            bootloader = firmware / "b300_bootloader_f407ze_com3_v00060500.hex"
            manifest_resource = firmware / "b300_bootloader_manifest.json"
            bootloader.write_bytes(b":00000001FF\n")
            manifest_resource.write_text("{}", encoding="utf-8")
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
                    "--gdb-root", str(gdb), "--gdb-archive", "xpack-gdb.zip",
                    "--gdb-sha256", "B" * 64,
                    "--bootstrap", str(bootstrap),
                    "--output", str(output),
                    "--platform", "windows-x64",
                    "--openocd-archive", "xpack-openocd-0.12.0-7-win32-x64.zip",
                    "--openocd-sha256", openocd_sha256,
                    "--openocd-package", str(xpack),
                    "--internal-distribution-approved",
                    "--resource", str(bootloader),
                    "--resource", str(manifest_resource),
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
        self.assertIn("resources/firmware/b300_bootloader_f407ze_com3_v00060500.hex", names)
        self.assertIn("resources/firmware/b300_bootloader_manifest.json", names)

    def test_internal_cli_zip_excludes_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = root / "b300-stlink"
            cli = application / "b300-stlink.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack-openocd.zip"
            (application / "_internal").mkdir(parents=True)
            (openocd / "bin").mkdir(parents=True)
            for path in (
                    cli, application / "_internal" / "python311.dll", bootstrap,
                    openocd / "bin" / "openocd.exe"):
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
                    "--application-root", str(application),
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
        self.assertIn("_internal/python311.dll", names)
        self.assertNotIn("b300-stlink-gui.exe", names)
        self.assertIn("vendor/openocd/bin/openocd.exe", names)
        self.assertIn("flavor=cli", metadata)

    def test_native_install_launchers_do_not_require_python_or_cubeide(self) -> None:
        for filename in ("install.ps1", "install.sh"):
            text = (ROOT / filename).read_text(encoding="utf-8").lower()
            self.assertNotIn("python", text)
            self.assertNotIn("cubeide", text)
            self.assertIn("b300-stlink", text)

    def test_native_bootstraps_validate_user_roots_and_overlap_before_copy(self) -> None:
        powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("IsPathRooted", powershell)
        self.assertIn("GetPathRoot", powershell)
        self.assertIn("UserProfile", powershell)
        self.assertIn("Test-PathWithin", powershell)
        self.assertLess(powershell.index("IsPathRooted"), powershell.index("Copy-Item"))
        self.assertLess(powershell.index("Test-PathWithin"), powershell.index("Copy-Item"))

        self.assertIn('case "${HOME-}"', shell)
        self.assertIn(
            'bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)', shell,
        )
        self.assertIn("pwd -P", shell)
        self.assertIn("path_within", shell)
        self.assertLess(shell.index('case "${HOME-}"'), shell.index("mkdir -p"))
        self.assertLess(shell.index("path_within"), shell.index("cp -a"))

    def test_native_bootstraps_validate_every_write_target_before_mutation(self) -> None:
        powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")

        windows_validation = powershell[:powershell.index(
            "New-Item -ItemType Directory -Force -Path $installRoot"
        )]
        self.assertIn("Assert-SafePathComponents", windows_validation)
        for target in (
                "$localAppData", "$installRoot", "$binRoot", "$cliLauncher",
                "$guiLauncher", "$appData", "$startMenu", "$shortcutPath"):
            with self.subTest(platform="windows", target=target):
                self.assertIn(target, windows_validation)

        shell_validation = shell[:shell.index('mkdir -p "$install_root"')]
        self.assertIn("reject_path_components", shell_validation)
        for target in (
                "$local_root", "$share_root", "$install_root", "$bin_root",
                "$cli_launcher", "$gui_launcher", "$applications_root",
                "$desktop_target", "$icons_root", "$icon_target"):
            with self.subTest(platform="linux", target=target):
                self.assertIn(target, shell_validation)
        self.assertNotIn('${HOME}/.local', shell)
        self.assertNotIn('$HOME/.local', shell)

    @unittest.skipUnless(os.name == "nt", "Windows reparse behavior test")
    def test_windows_bootstrap_rejects_reparse_ancestor_and_final_before_writes(self) -> None:
        powershell = _windows_tool(
            "powershell.exe",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        )
        if powershell is None:
            self.skipTest("Windows PowerShell is unavailable")
        user_profile = Path(subprocess.check_output(
            [
                powershell, "-NoProfile", "-NonInteractive", "-Command",
                "[Environment]::GetFolderPath('UserProfile')",
            ],
            text=True,
        ).strip())
        with tempfile.TemporaryDirectory(dir=str(user_profile)) as directory:
            root = Path(directory)
            for link_kind in ("ancestor", "final"):
                with self.subTest(link_kind=link_kind):
                    case_root = root / link_kind
                    bundle = case_root / "bundle"
                    bundle.mkdir(parents=True)
                    shutil.copy2(ROOT / "install.ps1", bundle / "install.ps1")
                    (bundle / "_internal").mkdir()
                    (bundle / "b300-stlink.exe").write_bytes(b"cli")
                    (bundle / "b300-stlink-gui.exe").write_bytes(b"gui")
                    outside = case_root / "outside"
                    if link_kind == "ancestor":
                        (outside / "Local").mkdir(parents=True)
                        link = case_root / "redirected"
                        local_app_data = link / "Local"
                        protected = outside / "Local" / "B300-STLink"
                    else:
                        local_app_data = case_root / "Local"
                        local_app_data.mkdir()
                        link = local_app_data / "B300-STLink"
                        protected = outside / "b300-stlink.exe"
                    _create_windows_junction(link, outside, powershell)
                    try:
                        environ = os.environ.copy()
                        environ["LOCALAPPDATA"] = str(local_app_data)
                        environ["APPDATA"] = str(Path(root.anchor) / "Windows")
                        result = subprocess.run(
                            [
                                powershell, "-NoProfile", "-NonInteractive",
                                "-ExecutionPolicy", "Bypass", "-File",
                                str(bundle / "install.ps1"),
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                            env=environ,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(
                            "reparse point", (result.stdout + result.stderr).lower()
                        )
                        self.assertFalse(protected.exists())
                    finally:
                        if os.path.lexists(str(link)):
                            link.rmdir()

    @unittest.skipUnless(os.name == "nt", "Git Bash junction behavior test")
    def test_posix_bootstrap_rejects_each_write_target_junction_before_writes(self) -> None:
        powershell = _windows_tool(
            "powershell.exe",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        )
        bash = _windows_tool("bash.exe", r"C:\Program Files\Git\bin\bash.exe")
        cygpath = _windows_tool("cygpath.exe", r"C:\Program Files\Git\usr\bin\cygpath.exe")
        if powershell is None or bash is None or cygpath is None:
            self.skipTest("Git Bash or Windows PowerShell is unavailable")
        cases = (
            ("local-ancestor", ".local"),
            ("install-root", ".local/share/b300-stlink"),
            ("bin-root", ".local/bin"),
            ("cli-launcher", ".local/bin/b300-stlink"),
            ("gui-launcher", ".local/bin/b300-stlink-gui"),
            ("applications-root", ".local/share/applications"),
            ("desktop-target", ".local/share/applications/b300-stlink-gui.desktop"),
            ("icons-ancestor", ".local/share/icons"),
            ("icons-root", ".local/share/icons/hicolor/scalable/apps"),
            ("icon-target", ".local/share/icons/hicolor/scalable/apps/b300-stlink-gui.svg"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, relative_link in cases:
                with self.subTest(target=label):
                    case_root = root / label
                    bundle = case_root / "bundle"
                    home = case_root / "home"
                    outside = case_root / "outside"
                    bundle.mkdir(parents=True)
                    home.mkdir()
                    shutil.copy2(ROOT / "install.sh", bundle / "install.sh")
                    for filename, payload in (
                            ("b300-stlink", b"#!/bin/sh\nexit 0\n"),
                            ("b300-stlink-gui", b"#!/bin/sh\nexit 0\n"),
                            ("b300-stlink-gui.desktop", b"desktop"),
                            ("b300-stlink-gui.svg", b"icon")):
                        path = bundle / filename
                        path.write_bytes(payload)
                        path.chmod(0o755)
                    link = home / Path(relative_link)
                    _create_windows_junction(link, outside, powershell)
                    try:
                        posix_home = subprocess.check_output(
                            [cygpath, "-u", str(home)], text=True,
                        ).strip()
                        posix_script = subprocess.check_output(
                            [cygpath, "-u", str(bundle / "install.sh")], text=True,
                        ).strip()
                        environ = os.environ.copy()
                        environ["HOME"] = posix_home
                        result = subprocess.run(
                            [bash, posix_script],
                            check=False,
                            capture_output=True,
                            text=True,
                            env=environ,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(
                            "unsafe symlink", (result.stdout + result.stderr).lower()
                        )
                        self.assertEqual(list(outside.iterdir()), [])
                    finally:
                        if os.path.lexists(str(link)):
                            link.rmdir()

    def test_windows_cli_packaging_requires_the_complete_onedir_application_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "b300-stlink.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack.zip"
            (openocd / "bin").mkdir(parents=True)
            for path in (cli, bootstrap, openocd / "bin" / "openocd.exe", xpack):
                path.write_bytes(b"runtime")
            manifest_digest = hashlib.sha256(
                package_internal.openocd_manifest(openocd)
            ).hexdigest()
            stderr = io.StringIO()
            with mock.patch.object(
                    package_internal, "TRUSTED_TREE_MANIFESTS",
                    {"windows-x64": manifest_digest},
            ), redirect_stderr(stderr), self.assertRaises(SystemExit):
                package_internal.main([
                    "--flavor", "cli", "--executable", str(cli),
                    "--openocd-root", str(openocd), "--bootstrap", str(bootstrap),
                    "--output", str(root / "cli.zip"), "--platform", "windows-x64",
                    "--openocd-archive", "xpack.zip", "--openocd-sha256", "A" * 64,
                    "--openocd-package", str(xpack), "--internal-distribution-approved",
                ])
            self.assertIn("application-root", stderr.getvalue())


    def test_base_gui_package_omits_gdb_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gui = root / "b300-stlink-gui.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack-openocd.zip"
            (openocd / "bin").mkdir(parents=True)
            for path in (gui, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"runtime")
            xpack.write_bytes(b"trusted")
            output = root / "gui.zip"
            manifest_digest = hashlib.sha256(package_internal.openocd_manifest(openocd)).hexdigest()
            with mock.patch.object(package_internal, "TRUSTED_TREE_MANIFESTS", {"windows-x64": manifest_digest}):
                package_internal.main([
                    "--flavor", "gui", "--executable", str(gui),
                    "--openocd-root", str(openocd), "--bootstrap", str(bootstrap),
                    "--output", str(output), "--platform", "windows-x64",
                    "--openocd-archive", "xpack-openocd.zip", "--openocd-sha256", "A" * 64,
                    "--openocd-package", str(xpack), "--internal-distribution-approved",
                ])
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                metadata = archive.read("BUNDLE-METADATA.txt").decode("ascii")
        self.assertFalse(any(name.startswith("vendor/gdb/") for name in names))
        self.assertNotIn("gdb=", metadata)

    def test_optional_gdb_arguments_must_be_complete_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gui = root / "b300-stlink-gui.exe"
            bootstrap = root / "install.ps1"
            openocd = root / "openocd"
            xpack = root / "xpack-openocd.zip"
            (openocd / "bin").mkdir(parents=True)
            for path in (gui, bootstrap, openocd / "bin" / "openocd.exe"):
                path.write_bytes(b"runtime")
            xpack.write_bytes(b"trusted")
            manifest_digest = hashlib.sha256(package_internal.openocd_manifest(openocd)).hexdigest()
            common = [
                "--flavor", "gui", "--executable", str(gui),
                "--openocd-root", str(openocd), "--bootstrap", str(bootstrap),
                "--output", str(root / "gui.zip"), "--platform", "windows-x64",
                "--openocd-archive", "xpack-openocd.zip", "--openocd-sha256", "A" * 64,
                "--openocd-package", str(xpack), "--internal-distribution-approved",
            ]
            with mock.patch.object(package_internal, "TRUSTED_TREE_MANIFESTS", {"windows-x64": manifest_digest}):
                for extra in (["--gdb-archive", "orphan.zip"], ["--gdb-sha256", "B" * 64]):
                    with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        package_internal.main(common + extra)

    def test_release_workflows_keep_base_gui_independent_of_bundled_gdb(self) -> None:
        for workflow_name in ("release.yml", "release-dry-run.yml"):
            workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8")
            normalized = workflow.replace("\\", "/")
            self.assertNotIn("vendor/gdb/bin/arm-none-eabi-gdb", normalized)

    def test_release_workflows_enforce_artifact_size_budgets(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        dry_run = (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(encoding="utf-8")
        for workflow in (release, dry_run):
            self.assertIn("scripts/release/check_size_budget.py", workflow)
            self.assertIn("B300-STLink-GUI-Windows-x64.zip --max-mib 80", workflow)
            self.assertIn("B300-STLink-CLI-Windows-x64.zip --max-mib 25", workflow)
            self.assertIn("B300-STLink-GUI-Windows-x64.exe --max-mib 90", workflow)
        self.assertIn("B300-STLink-GUI-Ubuntu-x64.AppImage --max-mib 220", release)
        self.assertIn("B300-STLink-GUI-Ubuntu-arm64.AppImage --max-mib 220", release)

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
            self.assertTrue((appdir / "usr" / "share" / "b300-stlink" / "udev" /
                             "49-b300-stlink.rules").is_file())
            self.assertFalse((appdir / "usr" / "bin" / "b300-stlink").exists())
            self.assertTrue((debroot / "DEBIAN" / "control").is_file())
            self.assertTrue((debroot / "DEBIAN" / "postinst").is_file())
            control_text = (debroot / "DEBIAN" / "control").read_text(encoding="utf-8")
            for dependency in (
                    "libxcb-cursor0", "libxcb-icccm4", "libxcb-keysyms1",
                    "libxcb-shape0", "libxkbcommon-x11-0"):
                self.assertIn(dependency, control_text)
            deb_launcher = (debroot / "usr" / "local" / "bin" /
                            "b300-stlink-gui").read_text(encoding="utf-8")
            self.assertIn("B300_APP_ROOT=/opt/b300-stlink", deb_launcher)
            app_run = (appdir / "AppRun").read_text(encoding="utf-8")
            self.assertIn("B300_APP_ROOT", app_run)
            udev_rule = (debroot / "usr" / "lib" / "udev" / "rules.d" /
                         "49-b300-stlink.rules")
            self.assertTrue(udev_rule.is_file())
            self.assertIn('ATTR{idVendor}=="0483"', udev_rule.read_text(encoding="utf-8"))
            self.assertIn('ATTR{idProduct}=="374?"', udev_rule.read_text(encoding="utf-8"))
            self.assertTrue((debroot / "usr" / "share" / "icons" / "hicolor" /
                             "512x512" / "apps" / "b300-stlink-gui.png").is_file())
            self.assertTrue((debroot / "usr" / "local" / "bin" /
                             "b300-stlink-gui").is_file())
            self.assertFalse((debroot / "usr" / "local" / "bin" /
                              "b300-stlink").exists())


if __name__ == "__main__":
    unittest.main()
