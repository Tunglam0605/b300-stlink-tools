import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from b300_cli.parser import parse_args
from b300_core import cli_update_install
from b300_core.release_manifest import ReleaseAsset
from b300_core.updater import DEFAULT_MANIFEST_URL, DEFAULT_SIGNATURE_URL
from tests.test_cli_update import FakeOpener, signed_manifest
from tests.test_release_manifest import TEST_PUBLIC_KEY


def _asset(package: Path, platform_name: str) -> ReleaseAsset:
    return ReleaseAsset(
        filename=package.name,
        url=(
            "https://github.com/TungLamAutomation/b300-stlink-tools/"
            "releases/download/v9.9.9/%s" % package.name
        ),
        size=package.stat().st_size,
        sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
    )


def _write_zip(path: Path, *, entries=None) -> None:
    selected = entries or {
        "b300-stlink.exe": b"cli",
        "_internal/python311.dll": b"runtime",
        "vendor/openocd/bin/openocd.exe": b"openocd",
        "install.ps1": b"bootstrap",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in selected.items():
            archive.writestr(name, data)


def _write_tar(path: Path, *, entries=None) -> None:
    selected = entries or {
        "b300-stlink": b"cli",
        "vendor/openocd/bin/openocd": b"openocd",
        "install.sh": b"bootstrap",
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in selected.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in {"b300-stlink", "vendor/openocd/bin/openocd"} else 0o644
            archive.addfile(info, io.BytesIO(data))


class SafeCliArchiveTests(unittest.TestCase):
    def test_zip_rejects_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case in ("traversal", "symlink"):
                package = root / "B300-STLink-CLI-Windows-x64.zip"
                with zipfile.ZipFile(package, "w") as archive:
                    if case == "traversal":
                        archive.writestr("../outside.txt", b"escape")
                    else:
                        link = zipfile.ZipInfo("b300-stlink.exe")
                        link.create_system = 3
                        link.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(link, "target.exe")
                    archive.writestr("install.ps1", b"bootstrap")
                with self.subTest(case=case), tempfile.TemporaryDirectory() as staging:
                    with self.assertRaisesRegex(ValueError, "unsafe|symlink|regular"):
                        cli_update_install.extract_verified_cli_bundle(
                            package, _asset(package, "windows-x64-cli"),
                            "windows-x64-cli", Path(staging),
                        )

    def test_tar_rejects_traversal_links_devices_and_special_files(self) -> None:
        cases = {
            "traversal": ("../outside", tarfile.REGTYPE, ""),
            "symlink": ("b300-stlink", tarfile.SYMTYPE, "target"),
            "hardlink": ("b300-stlink", tarfile.LNKTYPE, "target"),
            "character-device": ("dev", tarfile.CHRTYPE, ""),
            "block-device": ("dev", tarfile.BLKTYPE, ""),
            "fifo": ("pipe", tarfile.FIFOTYPE, ""),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case, (name, entry_type, linkname) in cases.items():
                package = root / "B300-STLink-CLI-Linux-x64.tar.gz"
                with tarfile.open(package, "w:gz") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = entry_type
                    entry.linkname = linkname
                    archive.addfile(entry, io.BytesIO(b"") if entry.isreg() else None)
                    bootstrap = tarfile.TarInfo("install.sh")
                    bootstrap.size = 1
                    archive.addfile(bootstrap, io.BytesIO(b"x"))
                with self.subTest(case=case), tempfile.TemporaryDirectory() as staging:
                    with self.assertRaisesRegex(ValueError, "unsafe|regular|special|link"):
                        cli_update_install.extract_verified_cli_bundle(
                            package, _asset(package, "linux-x64-cli"),
                            "linux-x64-cli", Path(staging),
                        )

    def test_entry_and_expanded_size_limits_are_preflighted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "B300-STLink-CLI-Windows-x64.zip"
            _write_zip(package)
            with tempfile.TemporaryDirectory() as staging, \
                    mock.patch.object(cli_update_install, "MAX_ARCHIVE_ENTRIES", 2):
                with self.assertRaisesRegex(ValueError, "too many entries"):
                    cli_update_install.extract_verified_cli_bundle(
                        package, _asset(package, "windows-x64-cli"),
                        "windows-x64-cli", Path(staging),
                    )
            with tempfile.TemporaryDirectory() as staging, \
                    mock.patch.object(cli_update_install, "MAX_EXPANDED_BYTES", 3):
                with self.assertRaisesRegex(ValueError, "expanded size"):
                    cli_update_install.extract_verified_cli_bundle(
                        package, _asset(package, "windows-x64-cli"),
                        "windows-x64-cli", Path(staging),
                    )

    def test_contract_recheck_rejects_wrong_filename_platform_size_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "B300-STLink-CLI-Windows-x64.zip"
            _write_zip(package)
            valid = _asset(package, "windows-x64-cli")
            cases = {
                "filename": ReleaseAsset("renamed.zip", valid.url, valid.size, valid.sha256),
                "size": ReleaseAsset(valid.filename, valid.url, valid.size + 1, valid.sha256),
                "digest": ReleaseAsset(valid.filename, valid.url, valid.size, "0" * 64),
            }
            for case, contract in cases.items():
                with self.subTest(case=case):
                    with self.assertRaises(ValueError):
                        cli_update_install.verify_cli_package(
                            package, contract, "windows-x64-cli"
                        )
            with self.assertRaisesRegex(ValueError, "platform|filename"):
                cli_update_install.verify_cli_package(
                    package, valid, "linux-x64-cli"
                )

    def test_bundle_requires_cli_executable_bootstrap_and_windows_onedir_runtime(self) -> None:
        required = {
            "executable": {
                "_internal/python311.dll": b"runtime", "install.ps1": b"bootstrap",
            },
            "bootstrap": {
                "b300-stlink.exe": b"cli", "_internal/python311.dll": b"runtime",
            },
            "onedir": {"b300-stlink.exe": b"cli", "install.ps1": b"bootstrap"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case, entries in required.items():
                package = root / "B300-STLink-CLI-Windows-x64.zip"
                _write_zip(package, entries=entries)
                with self.subTest(case=case), tempfile.TemporaryDirectory() as staging:
                    with self.assertRaisesRegex(ValueError, "executable|bootstrap|onedir"):
                        cli_update_install.extract_verified_cli_bundle(
                            package, _asset(package, "windows-x64-cli"),
                            "windows-x64-cli", Path(staging),
                        )

    def test_verified_bundle_is_staged_privately_without_touching_managed_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "B300-STLink-CLI-Linux-x64.tar.gz"
            _write_tar(package)
            managed = root / "home" / ".local" / "share" / "b300-stlink"
            managed.mkdir(parents=True)
            (managed / "old.txt").write_text("old", encoding="utf-8")
            staging_base = root / "cache"

            staged = cli_update_install.stage_verified_cli_bundle(
                package, _asset(package, "linux-x64-cli"), "linux-x64-cli",
                staging_base=staging_base,
            )

            self.assertTrue((staged.root / "b300-stlink").is_file())
            self.assertEqual((managed / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual(staged.root.parent.parent, staging_base.resolve())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(staged.root.parent.stat().st_mode), 0o700)


class ManagedCliInstallTests(unittest.TestCase):
    def test_staged_tree_hash_includes_permission_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "b300-stlink"
            executable.write_bytes(b"same bytes")
            writable_mode = stat.S_IREAD | stat.S_IWRITE
            readonly_mode = stat.S_IREAD
            try:
                executable.chmod(writable_mode)
                writable_hash = cli_update_install.hash_staged_tree(root)
                executable.chmod(readonly_mode)
                readonly_hash = cli_update_install.hash_staged_tree(root)
            finally:
                executable.chmod(writable_mode)
            self.assertNotEqual(writable_hash, readonly_hash)

    def test_managed_paths_are_fixed_and_system_roots_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "home" / "AppData" / "Local"
            windows = cli_update_install.managed_install_paths(
                "windows-x64-cli", environ={"LOCALAPPDATA": str(local)},
                home=root / "home",
            )
            self.assertEqual(windows.root, (local / "B300-STLink").resolve())
            self.assertEqual(windows.launcher, windows.root / "bin" / "b300-stlink.cmd")

            linux = cli_update_install.managed_install_paths(
                "linux-x64-cli", environ={}, home=root / "home",
            )
            self.assertEqual(
                linux.root, (root / "home" / ".local" / "share" / "b300-stlink").resolve()
            )
            self.assertEqual(
                linux.launcher, (root / "home" / ".local" / "bin" / "b300-stlink").resolve()
            )
            for target in (Path("/usr/local/b300-stlink"), Path("/opt/b300-stlink")):
                with self.subTest(target=target), self.assertRaisesRegex(
                        ValueError, "absolute|per-user|system"):
                    cli_update_install.validate_managed_root(target, root / "home")
            with self.assertRaisesRegex(ValueError, "absolute|system"):
                cli_update_install.managed_install_paths(
                    "windows-x64-cli", environ={"LOCALAPPDATA": "relative-cache"},
                    home=root / "home",
                )

    def test_managed_environment_bases_must_be_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            with self.assertRaisesRegex(ValueError, "absolute"):
                cli_update_install.managed_install_paths(
                    "linux-x64-cli", environ={"XDG_CACHE_HOME": "relative-cache"},
                    home=home,
                )

    def test_managed_environment_bases_refuse_the_filesystem_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = Path(root.anchor)
            cases = (
                ("windows-x64-cli", {"LOCALAPPDATA": str(anchor)}),
                ("linux-x64-cli", {"XDG_CACHE_HOME": str(anchor)}),
            )
            for platform_name, environ in cases:
                with self.subTest(platform=platform_name), self.assertRaisesRegex(
                        ValueError, "system root"):
                    cli_update_install.managed_install_paths(
                        platform_name, environ=environ, home=root / "home",
                    )

    def test_managed_roots_and_cache_are_proven_beneath_the_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            cases = (
                (
                    "windows-x64-cli",
                    {"LOCALAPPDATA": str(root / "ProgramData")},
                ),
                (
                    "linux-x64-cli",
                    {"XDG_CACHE_HOME": str(root / "system-cache")},
                ),
            )
            for platform_name, environ in cases:
                with self.subTest(platform=platform_name), self.assertRaisesRegex(
                        ValueError, "per-user|user home"):
                    cli_update_install.managed_install_paths(
                        platform_name, environ=environ, home=home,
                    )
            with self.assertRaisesRegex(ValueError, "absolute"):
                cli_update_install.managed_install_paths(
                    "linux-x64-cli", environ={}, home=Path("relative-home"),
                )
            with self.assertRaisesRegex(ValueError, "per-user|user home"):
                cli_update_install.validate_managed_root(root / "system" / "b300", home)

    def test_linux_launcher_is_lexical_and_rejects_symlink_parent_or_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for link_kind in ("parent", "target"):
                with self.subTest(link_kind=link_kind):
                    home = root / link_kind / "home"
                    outside = root / link_kind / "outside"
                    outside.mkdir(parents=True)
                    local = home / ".local"
                    local.mkdir(parents=True)
                    expected_launcher = local / "bin" / "b300-stlink"
                    try:
                        if link_kind == "parent":
                            (local / "bin").symlink_to(outside, target_is_directory=True)
                            protected = outside / "b300-stlink"
                        else:
                            (local / "bin").mkdir()
                            protected = outside / "protected"
                            protected.write_text("sentinel", encoding="utf-8")
                            expected_launcher.symlink_to(protected)
                    except OSError as error:
                        self.skipTest("symlinks unavailable: %s" % error)

                    with self.assertRaisesRegex(ValueError, "symlink"):
                        cli_update_install.managed_install_paths(
                            "linux-x64-cli", environ={}, home=home,
                        )
                    if link_kind == "parent":
                        self.assertFalse(protected.exists())
                    else:
                        self.assertEqual(protected.read_text(encoding="utf-8"), "sentinel")

    def test_linux_launcher_rejects_simulated_symlink_components_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for unsafe_part in ("parent", "target"):
                with self.subTest(unsafe_part=unsafe_part):
                    home = root / unsafe_part / "home"
                    paths = cli_update_install.managed_install_paths(
                        "linux-x64-cli", environ={}, home=home,
                    )
                    staged = paths.staging_base / "cli-install-test" / "application"
                    (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
                    (staged / "b300-stlink").write_text("new", encoding="utf-8")
                    (staged / "install.sh").write_text("bootstrap", encoding="utf-8")
                    (staged / "vendor" / "openocd" / "bin" / "openocd").write_text(
                        "openocd", encoding="utf-8"
                    )
                    unsafe = paths.launcher.parent if unsafe_part == "parent" else paths.launcher
                    original_is_symlink = Path.is_symlink

                    def simulated_is_symlink(candidate):
                        return candidate == unsafe or original_is_symlink(candidate)

                    with mock.patch.object(
                            Path, "is_symlink", autospec=True,
                            side_effect=simulated_is_symlink,
                    ), self.assertRaisesRegex(ValueError, "symlink"):
                        cli_update_install.apply_staged_cli_install(
                            staged, "linux-x64-cli", parent_pid=123,
                            environ={}, home=home, wait_parent=lambda _pid: None,
                        )
                    self.assertFalse(paths.root.exists())

    def test_launcher_is_rechecked_after_parent_exit_before_atomic_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            paths = cli_update_install.managed_install_paths(
                "linux-x64-cli", environ={}, home=home,
            )
            staged = paths.staging_base / "cli-install-test" / "application"
            (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
            (staged / "b300-stlink").write_text("new", encoding="utf-8")
            (staged / "install.sh").write_text("bootstrap", encoding="utf-8")
            (staged / "vendor" / "openocd" / "bin" / "openocd").write_text(
                "openocd", encoding="utf-8"
            )
            state = {"parent_exited": False}
            original_is_symlink = Path.is_symlink

            def wait_parent(_pid):
                state["parent_exited"] = True

            def simulated_is_symlink(candidate):
                return (
                    state["parent_exited"] and candidate == paths.launcher.parent
                ) or original_is_symlink(candidate)

            with mock.patch.object(
                    Path, "is_symlink", autospec=True,
                    side_effect=simulated_is_symlink,
            ), self.assertRaisesRegex(ValueError, "symlink"):
                cli_update_install.apply_staged_cli_install(
                    staged, "linux-x64-cli", parent_pid=123,
                    environ={}, home=home, wait_parent=wait_parent,
                )

            self.assertTrue(state["parent_exited"])
            self.assertFalse(paths.root.exists())

    def test_replacement_failure_restores_previous_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            paths = cli_update_install.managed_install_paths(
                "linux-x64-cli", environ={}, home=home,
            )
            paths.root.mkdir(parents=True)
            (paths.root / "old.txt").write_text("old", encoding="utf-8")
            staged = paths.staging_base / "cli-install-test" / "application"
            (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
            (staged / "b300-stlink").write_text("new", encoding="utf-8")
            (staged / "install.sh").write_text("bootstrap", encoding="utf-8")
            (staged / "vendor" / "openocd" / "bin" / "openocd").write_text(
                "openocd", encoding="utf-8"
            )
            calls = []

            def fail_second_replace(source, destination):
                calls.append((Path(source), Path(destination)))
                if len(calls) == 2:
                    raise OSError("simulated publish failure")
                return os.replace(source, destination)

            with self.assertRaisesRegex(OSError, "simulated"):
                cli_update_install.apply_staged_cli_install(
                    staged, "linux-x64-cli", parent_pid=123,
                    environ={}, home=home, wait_parent=lambda _pid: None,
                    replace=fail_second_replace,
                )

            self.assertEqual((paths.root / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((paths.root / "b300-stlink").exists())

    def test_success_waits_for_parent_replaces_tree_writes_launcher_and_durable_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            paths = cli_update_install.managed_install_paths(
                "linux-x64-cli", environ={}, home=home,
            )
            paths.root.mkdir(parents=True)
            (paths.root / "old.txt").write_text("old", encoding="utf-8")
            staged = paths.staging_base / "cli-install-test" / "application"
            (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
            executable = staged / "b300-stlink"
            executable.write_text("new", encoding="utf-8")
            executable.chmod(0o755)
            (staged / "install.sh").write_text("bootstrap", encoding="utf-8")
            (staged / "vendor" / "openocd" / "bin" / "openocd").write_text(
                "openocd", encoding="utf-8"
            )
            waited = []

            result = cli_update_install.apply_staged_cli_install(
                staged, "linux-x64-cli", parent_pid=321,
                environ={}, home=home, wait_parent=waited.append,
            )

            self.assertEqual(result, 0)
            self.assertEqual(waited, [321])
            self.assertEqual((paths.root / "b300-stlink").read_text(encoding="utf-8"), "new")
            launcher = paths.launcher.read_text(encoding="utf-8")
            self.assertIn("../share/b300-stlink", launcher)
            self.assertNotIn("python", launcher.lower())
            record = json.loads(paths.result_log.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["platform"], "linux-x64-cli")

    def test_durable_result_failure_rolls_back_the_previous_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            paths = cli_update_install.managed_install_paths(
                "linux-x64-cli", environ={}, home=home,
            )
            paths.root.mkdir(parents=True)
            (paths.root / "old.txt").write_text("old", encoding="utf-8")
            staged = paths.staging_base / "cli-install-test" / "application"
            (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
            (staged / "b300-stlink").write_text("new", encoding="utf-8")
            (staged / "install.sh").write_text("bootstrap", encoding="utf-8")
            (staged / "vendor" / "openocd" / "bin" / "openocd").write_text(
                "openocd", encoding="utf-8"
            )

            with mock.patch.object(
                    cli_update_install, "_write_result",
                    side_effect=OSError("simulated durable log failure"),
            ), self.assertRaisesRegex(OSError, "durable log"):
                cli_update_install.apply_staged_cli_install(
                    staged, "linux-x64-cli", parent_pid=123,
                    environ={}, home=home, wait_parent=lambda _pid: None,
                )

            self.assertEqual((paths.root / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((paths.root / "b300-stlink").exists())

    def test_windows_managed_launcher_targets_executable_beside_onedir_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "home" / "AppData" / "Local"
            paths = cli_update_install.managed_install_paths(
                "windows-x64-cli", environ={"LOCALAPPDATA": str(local)}, home=root / "home",
            )
            staged = paths.staging_base / "cli-install-test" / "application"
            (staged / "_internal").mkdir(parents=True)
            (staged / "vendor" / "openocd" / "bin").mkdir(parents=True)
            (staged / "b300-stlink.exe").write_bytes(b"cli")
            (staged / "_internal" / "python311.dll").write_bytes(b"runtime")
            (staged / "vendor" / "openocd" / "bin" / "openocd.exe").write_bytes(b"openocd")
            (staged / "install.ps1").write_bytes(b"bootstrap")

            cli_update_install.apply_staged_cli_install(
                staged, "windows-x64-cli", parent_pid=123,
                environ={"LOCALAPPDATA": str(local)}, home=root / "home",
                wait_parent=lambda _pid: None,
            )

            self.assertTrue(paths.executable.is_file())
            self.assertTrue((paths.root / "_internal" / "python311.dll").is_file())
            launcher = paths.launcher.read_text(encoding="ascii")
            self.assertIn(r"%~dp0..\b300-stlink.exe", launcher)

    def test_production_handoff_uses_staged_helper_argv_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "home" / "AppData" / "Local"
            paths = cli_update_install.managed_install_paths(
                "windows-x64-cli", environ={"LOCALAPPDATA": str(local)}, home=root / "home",
            )
            paths.root.mkdir(parents=True)
            running = paths.root / "b300-stlink.exe"
            running.write_bytes(b"old")
            package = root / "B300-STLink-CLI-Windows-x64.zip"
            _write_zip(package)
            calls = []

            handoff = cli_update_install.launch_managed_cli_install(
                package, _asset(package, "windows-x64-cli"), "windows-x64-cli",
                environ={"LOCALAPPDATA": str(local)}, home=root / "home",
                current_executable=running, frozen=True, parent_pid=777,
                spawner=lambda argv, **kwargs: calls.append((argv, kwargs)),
            )

            self.assertEqual(len(calls), 1)
            argv, kwargs = calls[0]
            self.assertIsInstance(argv, list)
            self.assertEqual(Path(argv[0]), handoff.staged.executable)
            self.assertIn("--apply-cli-update", argv)
            self.assertIn("777", argv)
            self.assertFalse(kwargs["shell"])
            self.assertNotEqual(handoff.staged.root, paths.root)

    def test_source_or_portable_execution_is_stably_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "B300-STLink-CLI-Linux-x64.tar.gz"
            _write_tar(package)
            with self.assertRaises(cli_update_install.ManagedInstallUnsupported) as captured:
                cli_update_install.launch_managed_cli_install(
                    package, _asset(package, "linux-x64-cli"), "linux-x64-cli",
                    environ={}, home=root / "home", current_executable=root / "portable" / "b300-stlink",
                    frozen=False, spawner=lambda *_args, **_kwargs: self.fail("must not spawn"),
                )
            self.assertEqual(captured.exception.reason_code, "MANAGED_INSTALL_UNSUPPORTED")


class CliInstallCommandTests(unittest.TestCase):
    def _runtime(self, manifest: bytes, signature: bytes, asset_url: str, payload: bytes):
        from b300_core.cli_update import build_cli_update_runtime

        opener = FakeOpener({
            DEFAULT_MANIFEST_URL: manifest,
            DEFAULT_SIGNATURE_URL: signature,
            asset_url: payload,
        })
        return build_cli_update_runtime(
            system="Windows", machine="AMD64", public_key=TEST_PUBLIC_KEY,
            open_url=opener,
        ), opener

    def _run(self, argv, runtime, **kwargs):
        from b300_cli.update_commands import run_update_command

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = run_update_command(
                parse_args(argv), "0.5.3", runtime=runtime, **kwargs
            )
        return code, json.loads(output.getvalue())

    def test_parser_exposes_update_install_and_self_update_alias(self) -> None:
        install = parse_args(["update", "install", "--verified-package", "bundle.zip", "--json"])
        alias = parse_args(["self-update", "--verified-package", "bundle.zip", "--json"])
        self.assertEqual((install.command, install.update_command), ("update", "install"))
        self.assertEqual(install.verified_package, Path("bundle.zip"))
        self.assertEqual(alias.command, "self-update")
        self.assertEqual(alias.update_command, "install")
        self.assertEqual(alias.verified_package, Path("bundle.zip"))

    def test_install_freshly_checks_downloads_and_hands_off_exact_signed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_fixture = root / "fixture.zip"
            _write_zip(package_fixture)
            payload = package_fixture.read_bytes()
            manifest, signature, asset_url = signed_manifest(payload=payload)
            runtime, opener = self._runtime(manifest, signature, asset_url, payload)
            handed_off = []

            with mock.patch(
                    "b300_cli.update_commands.launch_managed_cli_install",
                    side_effect=lambda package, asset, platform, **_kwargs:
                    handed_off.append((Path(package), asset, platform)) or mock.Mock(
                        result_log=root / "result.json"
                    )):
                code, value = self._run(
                    ["update", "install", "--json"], runtime,
                    environ={
                        "LOCALAPPDATA": str(root / "home" / "AppData" / "Local")
                    }, home=root / "home",
                )

            self.assertEqual(code, 0)
            self.assertEqual(value["command"], "update install")
            self.assertEqual(value["status"], "ok")
            self.assertTrue(value["handoff_started"])
            self.assertEqual(len(handed_off), 1)
            package, asset, platform = handed_off[0]
            self.assertEqual(package.name, "B300-STLink-CLI-Windows-x64.zip")
            self.assertEqual(asset.sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(platform, "windows-x64-cli")
            self.assertEqual(
                opener.requests,
                [DEFAULT_MANIFEST_URL, DEFAULT_SIGNATURE_URL, asset_url],
            )

    def test_verified_package_is_accepted_only_against_the_fresh_signed_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "B300-STLink-CLI-Windows-x64.zip"
            _write_zip(package)
            payload = package.read_bytes()
            manifest, signature, asset_url = signed_manifest(payload=payload)
            runtime, opener = self._runtime(manifest, signature, asset_url, payload)

            with mock.patch(
                    "b300_cli.update_commands.launch_managed_cli_install",
                    return_value=mock.Mock(result_log=root / "result.json")) as installer:
                code, value = self._run([
                    "self-update", "--verified-package", str(package), "--json",
                ], runtime)

            self.assertEqual(code, 0)
            self.assertEqual(value["command"], "self-update")
            installer.assert_called_once()
            self.assertEqual(Path(installer.call_args.args[0]), package.resolve())
            self.assertEqual(
                opener.requests, [DEFAULT_MANIFEST_URL, DEFAULT_SIGNATURE_URL],
            )

    def test_invalid_verified_package_falls_back_to_fresh_signed_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.zip"
            _write_zip(fixture)
            payload = fixture.read_bytes()
            altered = bytearray(payload)
            altered[-1] ^= 0x01
            cases = (
                ("missing", "B300-STLink-CLI-Windows-x64.zip", None),
                ("wrong-platform", "B300-STLink-CLI-Linux-x64.tar.gz", payload),
                ("wrong-size", "B300-STLink-CLI-Windows-x64.zip", b"stale"),
                ("wrong-sha", "B300-STLink-CLI-Windows-x64.zip", bytes(altered)),
            )
            for case, filename, supplied_payload in cases:
                with self.subTest(case=case):
                    case_root = root / case
                    case_root.mkdir()
                    supplied = case_root / filename
                    if supplied_payload is not None:
                        supplied.write_bytes(supplied_payload)
                    manifest, signature, asset_url = signed_manifest(payload=payload)
                    runtime, opener = self._runtime(
                        manifest, signature, asset_url, payload,
                    )
                    with mock.patch(
                            "b300_cli.update_commands.launch_managed_cli_install",
                            return_value=mock.Mock(
                                result_log=case_root / "result.json"
                            ),
                    ) as installer:
                        code, value = self._run([
                            "update", "install", "--verified-package", str(supplied),
                            "--json",
                        ], runtime, environ={
                            "LOCALAPPDATA": str(
                                case_root / "home" / "AppData" / "Local"
                            ),
                        }, home=case_root / "home")

                    self.assertEqual(code, 0)
                    self.assertTrue(value["downloaded"])
                    installer.assert_called_once()
                    installed = Path(installer.call_args.args[0])
                    self.assertEqual(
                        installed.name, "B300-STLink-CLI-Windows-x64.zip"
                    )
                    self.assertNotEqual(installed, supplied.resolve())
                    self.assertEqual(
                        opener.requests,
                        [DEFAULT_MANIFEST_URL, DEFAULT_SIGNATURE_URL, asset_url],
                    )

    def test_invalid_signature_sha_and_platform_never_reach_installer(self) -> None:
        cases = []
        manifest, signature, asset_url = signed_manifest()
        cases.append(("signature", manifest.replace(b"0.5.4", b"9.5.4"), signature, asset_url))
        manifest, signature, asset_url = signed_manifest(sha256="0" * 64)
        cases.append(("sha", manifest, signature, asset_url))
        manifest, signature, asset_url = signed_manifest(
            platform_key="linux-x64-cli", filename="B300-STLink-CLI-Linux-x64.tar.gz"
        )
        cases.append(("platform", manifest, signature, asset_url))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case, manifest, signature, asset_url in cases:
                runtime, _opener = self._runtime(
                    manifest, signature, asset_url, b"verified CLI update archive"
                )
                with self.subTest(case=case), mock.patch(
                        "b300_cli.update_commands.launch_managed_cli_install") as installer:
                    code, _value = self._run(
                        ["update", "install", "--json"], runtime,
                        environ={
                            "LOCALAPPDATA": str(root / "home" / "AppData" / case)
                        }, home=root / "home",
                    )
                    self.assertNotEqual(code, 0)
                    installer.assert_not_called()

    def test_source_execution_reports_safe_manual_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_fixture = root / "fixture.zip"
            _write_zip(package_fixture)
            payload = package_fixture.read_bytes()
            manifest, signature, asset_url = signed_manifest(payload=payload)
            runtime, _opener = self._runtime(manifest, signature, asset_url, payload)

            code, value = self._run(
                ["update", "install", "--json"], runtime,
                environ={
                    "LOCALAPPDATA": str(root / "home" / "AppData" / "Local")
                }, home=root / "home",
            )

            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "MANAGED_INSTALL_UNSUPPORTED")
            self.assertIn("download", value["message"].lower())
            self.assertIn("install", value["message"].lower())


if __name__ == "__main__":
    unittest.main()
