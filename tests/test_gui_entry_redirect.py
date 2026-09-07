from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from b300_core.runtime_integrity import write_runtime_manifest
from b300_gui_entry import find_canonical_gui_redirect


class GuiEntryRedirectTests(unittest.TestCase):
    def test_temporary_gui_redirects_to_verified_canonical_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary_root = root / "Temp"
            current = temporary_root / "stale" / "b300-stlink-gui.exe"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"stale")

            canonical_root = root / "LocalAppData" / "B300-STLink"
            canonical = canonical_root / "b300-stlink-gui.exe"
            canonical_root.mkdir(parents=True)
            canonical.write_bytes(b"canonical")
            (canonical_root / "BUNDLE-METADATA.txt").write_text(
                "platform=windows-x64\nflavor=gui\nversion=9.8.7\n",
                encoding="utf-8",
            )
            write_runtime_manifest(canonical_root, "9.8.7")

            redirect = find_canonical_gui_redirect(
                current,
                local_app_data=root / "LocalAppData",
                temporary_root=temporary_root,
                frozen=True,
            )

            self.assertEqual(redirect, canonical)

    def test_normal_installed_gui_does_not_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "LocalAppData" / "B300-STLink" / "b300-stlink-gui.exe"
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"canonical")

            self.assertIsNone(find_canonical_gui_redirect(
                canonical,
                local_app_data=root / "LocalAppData",
                temporary_root=root / "Temp",
                frozen=True,
            ))

    def test_temporary_gui_does_not_redirect_to_older_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "Temp" / "new" / "b300-stlink-gui.exe"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"new")
            canonical_root = root / "LocalAppData" / "B300-STLink"
            canonical = canonical_root / "b300-stlink-gui.exe"
            canonical_root.mkdir(parents=True)
            canonical.write_bytes(b"old")
            (canonical_root / "BUNDLE-METADATA.txt").write_text(
                "platform=windows-x64\nflavor=gui\nversion=0.21.4\n",
                encoding="utf-8",
            )
            write_runtime_manifest(canonical_root, "0.21.4")

            self.assertIsNone(find_canonical_gui_redirect(
                current,
                local_app_data=root / "LocalAppData",
                temporary_root=root / "Temp",
                frozen=True,
                current_version="0.21.5",
            ))

    def test_temporary_gui_does_not_redirect_to_corrupt_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "Temp" / "stale" / "b300-stlink-gui.exe"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"stale")
            canonical_root = root / "LocalAppData" / "B300-STLink"
            canonical = canonical_root / "b300-stlink-gui.exe"
            canonical_root.mkdir(parents=True)
            canonical.write_bytes(b"canonical")
            (canonical_root / "BUNDLE-METADATA.txt").write_text(
                "platform=windows-x64\nflavor=gui\nversion=9.8.7\n",
                encoding="utf-8",
            )
            write_runtime_manifest(canonical_root, "9.8.7")
            canonical.write_bytes(b"tampered")

            self.assertIsNone(find_canonical_gui_redirect(
                current,
                local_app_data=root / "LocalAppData",
                temporary_root=root / "Temp",
                frozen=True,
            ))

    def test_missing_local_app_data_never_uses_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "Temp" / "stale" / "b300-stlink-gui.exe"
            current.parent.mkdir(parents=True)
            current.write_bytes(b"stale")
            canonical_root = root / "B300-STLink"
            canonical = canonical_root / "b300-stlink-gui.exe"
            canonical_root.mkdir()
            canonical.write_bytes(b"canonical")
            (canonical_root / "BUNDLE-METADATA.txt").write_text(
                "platform=windows-x64\nflavor=gui\nversion=9.8.7\n",
                encoding="utf-8",
            )
            write_runtime_manifest(canonical_root, "9.8.7")

            previous = Path.cwd()
            os.chdir(root)
            try:
                self.assertIsNone(find_canonical_gui_redirect(
                    current,
                    temporary_root=root / "Temp",
                    frozen=True,
                    environment={},
                ))
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
