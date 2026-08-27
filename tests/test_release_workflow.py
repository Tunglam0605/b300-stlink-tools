import unittest
from pathlib import Path

import yaml

from scripts.release.release_contract import EXPECTED_PACKAGE_ASSETS


ROOT = Path(__file__).resolve().parents[1]


def load_workflow(name: str):
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


class ReleaseWorkflowTests(unittest.TestCase):
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
        self.assertIn("gh release create", commands)
        self.assertIn("--draft", commands)
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

    def test_workflow_mentions_every_downloadable_package(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        for name in EXPECTED_PACKAGE_ASSETS:
            with self.subTest(name=name):
                self.assertIn(name, text)

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


if __name__ == "__main__":
    unittest.main()
