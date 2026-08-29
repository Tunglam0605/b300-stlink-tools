import unittest
from pathlib import Path

import yaml

from scripts.release.release_contract import (
    EXPECTED_PACKAGE_ASSETS,
    METADATA_ASSETS,
)


ROOT = Path(__file__).resolve().parents[1]


def load_workflow(name: str):
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


class ReleaseWorkflowTests(unittest.TestCase):
    def test_windows_dry_run_smokes_the_gui_onedir_executable(self) -> None:
        text = (
            ROOT / ".github" / "workflows" / "release-dry-run.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            r"-FilePath .\release\b300-stlink-gui\b300-stlink-gui.exe", text,
        )
        self.assertNotIn(
            r"-FilePath .\release\b300-stlink-gui.exe", text,
        )

    def test_official_release_is_tag_only_and_publish_is_least_privileged(self) -> None:
        workflow = load_workflow("release.yml")
        self.assertEqual(workflow["on"], {"push": {"tags": ["v*"]}})
        self.assertEqual(workflow["permissions"]["contents"], "read")
        finalize = workflow["jobs"]["finalize-release"]
        self.assertEqual(finalize["permissions"]["contents"], "write")
        self.assertEqual(
            set(finalize["needs"]),
            {"prepare-release", "build-windows", "build-linux-x64", "build-linux-arm64"},
        )
        commands = "\n".join(
            step.get("run", "") for step in finalize["steps"] if isinstance(step, dict)
        )
        self.assertIn("gh release edit", commands)
        downloads = [
            step.get("with", {}).get("name")
            for step in finalize["steps"]
            if isinstance(step, dict) and "actions/download-artifact@" in step.get("uses", "")
        ]
        self.assertEqual(
            downloads,
            ["release-windows-x64", "release-linux-x64", "release-linux-arm64", "release-notes"],
        )

    def test_release_uses_action_uploader_for_only_the_signed_root_assets(self) -> None:
        workflow = load_workflow("release.yml")
        finalize_steps = workflow["jobs"]["finalize-release"]["steps"]
        uploader = next(
            step for step in finalize_steps
            if step.get("name") == "Create draft release and upload exact assets"
        )
        self.assertTrue(uploader["uses"].startswith("softprops/action-gh-release@"))
        self.assertEqual(uploader["with"]["draft"], "true")
        self.assertEqual(uploader["with"]["fail_on_unmatched_files"], "true")
        self.assertEqual(uploader["with"]["overwrite_files"], "true")
        asset_paths = uploader["with"]["files"]
        for asset in EXPECTED_PACKAGE_ASSETS + METADATA_ASSETS:
            with self.subTest(asset=asset):
                self.assertIn("release-assets/" + asset, asset_paths)

        commands = "\n".join(
            step.get("run", "") for step in finalize_steps if isinstance(step, dict)
        )
        self.assertNotIn("gh release create", commands)

    def test_linux_release_builds_inside_ubuntu_2204_userspace(self) -> None:
        workflow = load_workflow("release.yml")
        for job_name in ("build-linux-x64", "build-linux-arm64"):
            job = workflow["jobs"][job_name]
            self.assertEqual(job["container"], "ubuntu:22.04")

    def test_linux_release_installs_glib_for_the_packaged_gui_smoke_test(self) -> None:
        for name in ("release.yml", "release-dry-run.yml"):
            with self.subTest(workflow=name):
                text = (ROOT / ".github" / "workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("libglib2.0-0", text)
                self.assertIn("libdbus-1-3", text)
                self.assertIn(" file ", text)

    def test_workflow_mentions_every_downloadable_package(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        for name in EXPECTED_PACKAGE_ASSETS:
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_final_release_flattens_downloaded_artifacts_before_metadata(self) -> None:
        workflow = load_workflow("release.yml")
        finalize_steps = workflow["jobs"]["finalize-release"]["steps"]
        commands = "\n".join(
            step.get("run", "") for step in finalize_steps if isinstance(step, dict)
        )
        self.assertIn("find release-assets -mindepth 2 -type f", commands)

    def test_release_uses_checksum_pinned_minisign_binary(self) -> None:
        workflow = load_workflow("release.yml")
        finalize_steps = workflow["jobs"]["finalize-release"]["steps"]
        signing = next(
            step for step in finalize_steps
            if step.get("name") == "Sign and verify release manifests"
        )
        self.assertIn("minisign-0.12-linux.tar.gz", signing["run"])
        self.assertIn(
            "9A599B48BA6EB7B1E80F12F36B94CECA7C00B7A5173C95C3EFC88D9822957E73",
            signing["run"],
        )
        self.assertNotIn("apt-get install -y minisign", signing["run"])
        self.assertIn("latest-cli.json", signing["run"])
        self.assertIn("latest-cli.json.minisig", signing["run"])

    def test_release_verifies_public_signed_updater_after_publish(self) -> None:
        workflow = load_workflow("release.yml")
        finalize_steps = workflow["jobs"]["finalize-release"]["steps"]
        verify = next(
            step for step in finalize_steps
            if step.get("name") == "Verify published signed updater state"
        )
        command = verify["run"]
        self.assertIn("scripts.release.verify_published", command)
        self.assertIn("releases/latest/download/latest.json", command)
        self.assertIn("latest.json.minisig", command)
        self.assertIn("releases/latest/download/latest-cli.json", command)
        self.assertIn("latest-cli.json.minisig", command)
        self.assertIn("--audience gui", command)
        self.assertIn("--audience cli", command)
        self.assertIn("MINISIGN_PUBLIC_KEY", verify.get("env", {}))
        publish_index = next(
            index for index, step in enumerate(finalize_steps)
            if step.get("name") == "Publish as Latest after upload validation"
        )
        verify_index = finalize_steps.index(verify)
        self.assertGreater(verify_index, publish_index)

    def test_development_packages_run_on_develop_branches_without_publish_permissions(self) -> None:
        workflow = load_workflow("release-dry-run.yml")
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["on"]["push"]["branches"], ["develop/**"])
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "true")
        self.assertIn("development-packages-", workflow["concurrency"]["group"])
        text = (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("retention-days: 7"), 2)
        self.assertNotIn("gh release create", text)
        self.assertNotIn("contents: write", text)

    def test_download_guide_separates_stable_from_development_builds(self) -> None:
        text = (ROOT / "DOWNLOAD.md").read_text(encoding="utf-8")
        self.assertIn("Development Build", text)
        self.assertIn("develop/**", text)
        self.assertIn("không", text[text.index("Development Build"):].lower())
        self.assertIn("Latest", text)
        self.assertIn("7 ngày", text)

    def test_dry_run_cannot_publish_release(self) -> None:
        workflow = load_workflow("release-dry-run.yml")
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["permissions"]["contents"], "read")
        text = (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("gh release create", text)
        self.assertNotIn("contents: write", text)

    def test_dry_run_builds_every_final_downloadable_package(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(
            encoding="utf-8"
        )
        for name in EXPECTED_PACKAGE_ASSETS:
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_packaged_cli_smoke_includes_single_machine_debug_selftest(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        development = (ROOT / ".github" / "workflows" / "release-dry-run.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(release.count("debug selftest --symbols"), 3)
        self.assertGreaterEqual(development.count("debug selftest --symbols"), 2)
        for workflow in (release, development):
            self.assertIn("--dry-run --json", workflow)
            self.assertIn("selftest-smoke.axf", workflow)

    def test_linux_release_smoke_tests_detached_update_helper(self) -> None:
        for name in ("release.yml", "release-dry-run.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
                self.assertIn("--apply-verified-update --help", workflow)
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(release.count("--apply-verified-update --help"), 2)

    def test_linux_release_uses_x11_smoke_test(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("xauth xvfb", workflow)
        self.assertGreaterEqual(workflow.count("Smoke-test Linux GUI on X11"), 2)
        self.assertGreaterEqual(workflow.count("env -u QT_QPA_PLATFORM xvfb-run -a"), 2)

    def test_release_workflows_smoke_debug_selftest_without_probe_access(self) -> None:
        for name in ("release.yml", "release-dry-run.yml"):
            with self.subTest(workflow=name):
                text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
                self.assertIn("debug selftest --symbols", text)
                self.assertGreaterEqual(text.count("selftest-smoke.axf"), 2)
                self.assertGreaterEqual(text.count("--dry-run --json"), 2)

    def test_all_native_cli_archives_are_staged_and_smoked_without_probe_access(self) -> None:
        for name in ("release.yml", "release-dry-run.yml"):
            with self.subTest(workflow=name):
                workflow = load_workflow(name)
                text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
                self.assertIn("B300-STLink-CLI-Windows-x64.zip", text)
                self.assertIn("Expand-Archive", text)
                self.assertIn("windows-cli-bundle", text)
                self.assertIn("b300-stlink.exe --help", text)
                self.assertIn("b300-stlink.exe --version --json", text)
                self.assertIn("b300-stlink.exe doctor --json", text)
                self.assertIn("windows-cli-bundle\\vendor\\openocd\\bin\\openocd.exe", text)
                self.assertIn("tar -xzf", text)
                self.assertIn("linux-cli-bundle", text)
                self.assertIn("linux-cli-bundle/b300-stlink --help", text)
                self.assertIn("linux-cli-bundle/b300-stlink --version --json", text)
                self.assertIn("linux-cli-bundle/b300-stlink doctor --json", text)
                self.assertIn("linux-cli-bundle/vendor/openocd/bin/openocd --version", text)
                self.assertNotIn("sudo b300-stlink", text)



if __name__ == "__main__":
    unittest.main()
