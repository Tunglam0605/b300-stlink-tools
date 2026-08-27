import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.release.build_metadata import build_release_metadata
from scripts.release.release_contract import EXPECTED_PACKAGE_ASSETS


class ReleaseMetadataTests(unittest.TestCase):
    def _write_packages(self, root: Path) -> None:
        for index, name in enumerate(EXPECTED_PACKAGE_ASSETS):
            (root / name).write_bytes(("asset-%d-%s" % (index, name)).encode("utf-8"))

    def test_builds_deterministic_manifests_and_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_packages(root)
            kwargs = dict(
                asset_dir=root,
                version="0.3.0",
                commit="0123456789abcdef0123456789abcdef01234567",
                published_at="2026-08-27T12:34:56Z",
                base_url="https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.0",
                release_page="https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0",
                notes="Safe release.\nSecond line.",
            )
            build_release_metadata(**kwargs)
            first = {
                name: (root / name).read_bytes()
                for name in ("release-manifest.json", "latest.json", "SHA256SUMS.txt")
            }
            build_release_metadata(**kwargs)
            second = {name: (root / name).read_bytes() for name in first}
            self.assertEqual(first, second)

            manifest = json.loads(first["release-manifest.json"])
            latest = json.loads(first["latest.json"])
            self.assertEqual(manifest["version"], "0.3.0")
            self.assertEqual(manifest["openocd"], "0.12.0-7")
            self.assertEqual(set(manifest["assets"]), set(EXPECTED_PACKAGE_ASSETS))
            self.assertEqual(latest["schema_version"], 1)
            self.assertEqual(
                latest["platforms"]["windows-x64"]["file"],
                "B300-STLink-GUI-Windows-x64.exe",
            )
            self.assertTrue(
                latest["platforms"]["linux-x64-appimage"]["url"].endswith(
                    "/B300-STLink-GUI-Ubuntu-x64.AppImage"
                )
            )
            checksum_lines = first["SHA256SUMS.txt"].decode("ascii").splitlines()
            self.assertEqual(len(checksum_lines), len(EXPECTED_PACKAGE_ASSETS) + 2)
            expected_digest = hashlib.sha256(first["latest.json"]).hexdigest()
            self.assertIn(expected_digest + "  latest.json", checksum_lines)

    def test_metadata_writer_is_compatible_with_python_39_pathlib(self) -> None:
        original_write_text = Path.write_text

        def python39_write_text(path, data, encoding=None, errors=None):
            return original_write_text(path, data, encoding=encoding, errors=errors)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_packages(root)
            with mock.patch.object(Path, "write_text", python39_write_text):
                build_release_metadata(
                    root, "0.3.0", "a" * 40, "2026-08-27T00:00:00Z",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.0",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0",
                    "Notes",
                )
            self.assertEqual((root / "SHA256SUMS.txt").read_text("ascii").count("\n"),
                             len(EXPECTED_PACKAGE_ASSETS) + 2)

    def test_rejects_missing_and_unexpected_package_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_packages(root)
            (root / EXPECTED_PACKAGE_ASSETS[0]).unlink()
            with self.assertRaisesRegex(ValueError, "Missing release assets"):
                build_release_metadata(
                    root, "0.3.0", "a" * 40, "2026-08-27T00:00:00Z",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.0",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0",
                    "Notes",
                )
            self._write_packages(root)
            (root / "unexpected.exe").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "Unexpected release assets"):
                build_release_metadata(
                    root, "0.3.0", "a" * 40, "2026-08-27T00:00:00Z",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.0",
                    "https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v0.3.0",
                    "Notes",
                )

    def test_rejects_mutable_or_non_https_release_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_packages(root)
            for url in (
                "http://github.com/org/repo/releases/download/v0.3.0",
                "https://github.com/org/repo/releases/latest/download",
            ):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    build_release_metadata(
                        root, "0.3.0", "a" * 40, "2026-08-27T00:00:00Z", url,
                        "https://github.com/org/repo/releases/tag/v0.3.0", "Notes",
                    )


if __name__ == "__main__":
    unittest.main()
