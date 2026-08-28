import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from b300_cli.parser import parse_args
from b300_core import linux_usb


class LinuxUsbSetupTests(unittest.TestCase):
    def test_confirmed_setup_never_consults_a_path_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            class Result:
                returncode = 0

            trusted = {
                "pkexec": "/usr/bin/pkexec",
                "install": "/usr/bin/install",
                "udevadm": "/usr/bin/udevadm",
            }
            with mock.patch.object(
                    linux_usb, "resolve_trusted_system_executable",
                    side_effect=lambda name: trusted.get(name), create=True,
            ) as resolver:
                linux_usb.perform_linux_usb_setup(
                    system="Linux", rule_path=root / "rule",
                    install_requested=True, confirmed=True,
                    runner=lambda command, **kwargs: calls.append((command, kwargs)) or Result(),
                    staging_parent=root / "staging",
                )

            self.assertEqual(len(calls), 3)
            self.assertTrue(all(command[0].startswith("/usr/bin/") for command, _ in calls))
            self.assertEqual(
                [call.args[0] for call in resolver.call_args_list],
                ["pkexec", "install", "udevadm"],
            )

    def test_trusted_system_resolver_rejects_untrusted_or_writable_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trusted = root / "trusted"
            outside = root / "outside"
            trusted.mkdir()
            outside.mkdir()
            for path in (root, trusted, outside):
                path.chmod(0o755)
            trusted_binary = trusted / "install"
            trusted_binary.write_bytes(b"tool")
            trusted_binary.chmod(0o755)
            outside_binary = outside / "install"
            outside_binary.write_bytes(b"tool")
            outside_binary.chmod(0o755)
            owner_uid = trusted_binary.stat().st_uid

            resolver_kwargs = {
                "trusted_directories": (trusted,),
                "trust_anchor": root,
                "expected_owner_uid": owner_uid,
            }
            cases = (
                ("relative", Path("install"), "absolute"),
                ("outside", outside_binary, "trusted system directory"),
            )
            for label, candidate, message in cases:
                with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                    linux_usb.resolve_trusted_system_executable(
                        "install", candidates={"install": (candidate,)}, **resolver_kwargs,
                    )

            trusted_binary.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "writable"):
                linux_usb.resolve_trusted_system_executable(
                    "install", candidates={"install": (trusted_binary,)}, **resolver_kwargs,
                )
            trusted_binary.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "owner"):
                linux_usb.resolve_trusted_system_executable(
                    "install", candidates={"install": (trusted_binary,)},
                    trusted_directories=(trusted,), trust_anchor=root,
                    expected_owner_uid=owner_uid + 1,
                )

    def test_non_linux_is_unsupported_without_filesystem_or_runner_access(self) -> None:
        calls = []
        report = linux_usb.perform_linux_usb_setup(
            system="Windows", rule_path=Path("/must/not/be/read"),
            runner=lambda *_args, **_kwargs: calls.append("runner"),
            trusted_resolver=lambda _name: self.fail("tool lookup must not run"),
        )
        self.assertFalse(report.supported)
        self.assertEqual(report.reason_code, "LINUX_SETUP_UNSUPPORTED")
        self.assertEqual(calls, [])

    def test_existing_canonical_rule_is_read_only_and_requests_replug(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rule = Path(directory) / "49-b300-stlink.rules"
            rule.write_text(linux_usb.B300_UDEV_RULE, encoding="utf-8")
            calls = []
            report = linux_usb.perform_linux_usb_setup(
                system="Linux", rule_path=rule,
                runner=lambda *_args, **_kwargs: calls.append("runner"),
            )
            self.assertTrue(report.supported)
            self.assertTrue(report.rule_installed)
            self.assertEqual(report.reason_code, "UDEV_RULE_PRESENT")
            self.assertIn("replug", report.next_action.lower())
            self.assertEqual(calls, [])

    def test_missing_rule_defaults_to_dry_run_without_elevation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            report = linux_usb.perform_linux_usb_setup(
                system="Linux", rule_path=root / "etc" / "49-b300-stlink.rules",
                install_requested=False, confirmed=False,
                runner=lambda *_args, **_kwargs: calls.append("runner"),
                trusted_resolver=lambda _name: self.fail("tool lookup must not run"),
                staging_parent=root / "staging",
            )
            self.assertTrue(report.dry_run)
            self.assertEqual(report.reason_code, "UDEV_RULE_INSTALL_AVAILABLE")
            self.assertIn("--install-udev-rule", report.next_action)
            self.assertIn("--confirm-system-change", report.next_action)
            self.assertEqual(calls, [])
            self.assertFalse((root / "etc").exists())

    def test_both_flags_are_required_before_any_privileged_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for requested, confirmed in ((True, False), (False, True)):
                calls = []
                with self.subTest(requested=requested, confirmed=confirmed):
                    with self.assertRaises(linux_usb.SystemChangeConfirmationRequired) as captured:
                        linux_usb.perform_linux_usb_setup(
                            system="Linux", rule_path=root / "49-b300-stlink.rules",
                            install_requested=requested, confirmed=confirmed,
                            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                            trusted_resolver=lambda _name: self.fail("tool lookup must not run"),
                            staging_parent=root / "staging",
                        )
                    self.assertEqual(
                        captured.exception.reason_code,
                        "SYSTEM_CHANGE_CONFIRMATION_REQUIRED",
                    )
                    self.assertEqual(calls, [])

    def test_controlled_install_announces_then_elevates_only_exact_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []

            class Result:
                returncode = 0

            def runner(command, **kwargs):
                events.append(("run", command, kwargs))
                return Result()

            report = linux_usb.perform_linux_usb_setup(
                system="Linux", rule_path=root / "etc" / "49-b300-stlink.rules",
                install_requested=True, confirmed=True,
                runner=runner,
                trusted_resolver=lambda name: "/usr/bin/" + name,
                staging_parent=root / "staging",
                announce=lambda plan: events.append(("announce", plan.commands)),
            )

            self.assertEqual(events[0][0], "announce")
            runs = [event for event in events if event[0] == "run"]
            self.assertEqual(len(runs), 3)
            commands = [event[1] for event in runs]
            for command in commands:
                self.assertEqual(command[0], "/usr/bin/pkexec")
                self.assertNotIn("b300-stlink", command[1])
                self.assertNotIn("python", command[1])
            copy = commands[0]
            self.assertEqual(copy[1:6], ["/usr/bin/install", "-o", "root", "-g", "root"])
            self.assertIn("0644", copy)
            self.assertEqual(copy[-1], str((root / "etc" / "49-b300-stlink.rules").resolve()))
            staged_rule = Path(copy[-2])
            announced_copy = events[0][1][0]
            self.assertEqual(tuple(copy), announced_copy)
            reload_command, trigger_command = commands[1:]
            self.assertEqual(reload_command[1:], ["/usr/bin/udevadm", "control", "--reload-rules"])
            self.assertEqual(trigger_command[1:], [
                "/usr/bin/udevadm", "trigger", "--subsystem-match=usb",
                "--attr-match=idVendor=0483", "--attr-match=idProduct=374?",
            ])
            self.assertFalse(staged_rule.exists(), "private staging must be cleaned")
            for _kind, _command, kwargs in runs:
                self.assertFalse(kwargs["shell"])
                self.assertFalse(kwargs["check"])
            self.assertEqual(report.reason_code, "UDEV_RULE_INSTALLED")
            self.assertIn("replug", report.next_action.lower())

    def test_sudo_is_a_headless_fallback_when_pkexec_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            class Result:
                returncode = 0

            linux_usb.perform_linux_usb_setup(
                system="Linux", rule_path=root / "rule",
                install_requested=True, confirmed=True,
                runner=lambda command, **kwargs: calls.append((command, kwargs)) or Result(),
                trusted_resolver=(
                    lambda name: None if name == "pkexec" else "/usr/bin/" + name
                ),
                staging_parent=root / "staging",
            )
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(command[0] == "/usr/bin/sudo" for command, _kwargs in calls))
            self.assertTrue(all(kwargs["stdin"] is None for _command, kwargs in calls))

    def test_parser_exposes_read_only_setup_and_explicit_system_change_flags(self) -> None:
        dry_run = parse_args(["setup", "--json"])
        install = parse_args([
            "setup", "--install-udev-rule", "--confirm-system-change", "--json",
        ])
        self.assertEqual(dry_run.command, "setup")
        self.assertFalse(dry_run.install_udev_rule)
        self.assertFalse(dry_run.confirm_system_change)
        self.assertTrue(install.install_udev_rule)
        self.assertTrue(install.confirm_system_change)

    def test_gui_packaging_imports_the_same_canonical_rule_definition(self) -> None:
        path = Path(__file__).resolve().parents[1] / "packaging" / "build_gui.py"
        spec = importlib.util.spec_from_file_location("b300_test_gui_builder_rule", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertIs(module.B300_UDEV_RULE, linux_usb.B300_UDEV_RULE)

    def test_cli_setup_reports_dry_run_as_one_stable_snapshot(self) -> None:
        import b300_stlink

        report = linux_usb.LinuxUsbSetupReport(
            supported=True, rule_installed=False, dry_run=True, changed=False,
            reason_code="UDEV_RULE_INSTALL_AVAILABLE", message="missing",
            next_action="rerun with both flags", rule_path=Path("/etc/udev/rules.d/49-b300-stlink.rules"),
        )
        output = io.StringIO()
        with mock.patch.object(
                b300_stlink, "perform_linux_usb_setup", return_value=report,
        ), redirect_stdout(output), redirect_stderr(output):
            code = b300_stlink.main(["setup", "--json"])
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(value["command"], "setup")
        self.assertEqual(value["reason_code"], "UDEV_RULE_INSTALL_AVAILABLE")
        self.assertTrue(value["dry_run"])

    def test_cli_announces_exact_setup_argv_before_execution_result(self) -> None:
        import b300_stlink

        commands = (("/usr/bin/pkexec", "/usr/bin/udevadm", "control", "--reload-rules"),)
        plan = linux_usb.LinuxUsbSetupReport(
            supported=True, rule_installed=False, dry_run=False, changed=False,
            reason_code="UDEV_RULE_INSTALL_PLANNED", message="planned",
            next_action="authenticate", rule_path=Path("/etc/rule"), commands=commands,
        )
        result = linux_usb.LinuxUsbSetupReport(
            supported=True, rule_installed=True, dry_run=False, changed=True,
            reason_code="UDEV_RULE_INSTALLED", message="installed",
            next_action="replug", rule_path=Path("/etc/rule"), commands=commands,
        )

        def fake_setup(**kwargs):
            kwargs["announce"](plan)
            return result

        output = io.StringIO()
        with mock.patch.object(
                b300_stlink, "perform_linux_usb_setup", side_effect=fake_setup,
        ), redirect_stdout(output), redirect_stderr(output):
            code = b300_stlink.main([
                "setup", "--install-udev-rule", "--confirm-system-change", "--json",
            ])
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["event"], "setup_plan")
        self.assertEqual(records[0]["commands"], [list(commands[0])])
        self.assertEqual(records[1]["reason_code"], "UDEV_RULE_INSTALLED")


if __name__ == "__main__":
    unittest.main()
