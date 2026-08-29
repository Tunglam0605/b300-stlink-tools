from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = "RWSjwseDEGd6o+Ykylwi3nmXPA7DYtOhvuXvHBQxf58Dej383Hd+5eYN"


class CliBootstrapTests(unittest.TestCase):
    def test_windows_bootstrap_verifies_signed_manifest_and_package(self) -> None:
        text = (ROOT / "install-cli.ps1").read_text(encoding="utf-8")
        self.assertIn("latest-cli.json.minisig", text)
        self.assertIn(PUBLIC_KEY, text)
        self.assertIn("37b600344e20c19314b2e82813db2bfdcc408b77b876f7727889dbd46d539479", text)
        self.assertIn("minisign-0.12-win64.zip", text)
        self.assertIn("-Vm $manifestPath", text)
        self.assertIn("Get-Sha256 $package", text)
        self.assertIn("$asset.size", text)
        self.assertIn("releases/download/v", text)
        self.assertIn("install.ps1", text)
        self.assertIn("gateway doctor", text)
        self.assertNotIn("Start-Process powershell -Verb RunAs", text)

    def test_linux_bootstrap_verifies_signed_manifest_and_package_without_full_cli_sudo(self) -> None:
        text = (ROOT / "install-cli.sh").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\nset -eu\n"))
        self.assertIn("latest-cli.json.minisig", text)
        self.assertIn(PUBLIC_KEY, text)
        self.assertIn("9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73", text)
        self.assertIn("minisign-linux/$mini_arch/minisign", text)
        self.assertIn('"$minisign_bin" -Vm', text)
        self.assertIn("sha256sum", text)
        self.assertIn("tar -tzf", text)
        self.assertIn("install.sh", text)
        self.assertIn("gateway doctor", text)
        self.assertNotIn("sudo ", text)
        self.assertNotIn("pkexec ", text)

    def test_bootstrap_docs_expose_one_line_terminal_install(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        download = (ROOT / "DOWNLOAD.md").read_text(encoding="utf-8")
        linux = "curl -fsSL https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.sh | sh"
        windows = "irm https://raw.githubusercontent.com/Tunglam0605/b300-stlink-tools/main/install-cli.ps1 | iex"
        for text in (readme, download):
            self.assertIn(linux, text)
            self.assertIn(windows, text)
            self.assertIn("b300-stlink gateway doctor", text)


if __name__ == "__main__":
    unittest.main()
