from __future__ import annotations

import hashlib
import importlib.util
import importlib
import os
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import package_internal
from b300_version import __version__


class RuntimeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('b300_core.runtime_integrity'),
                             'Complete runtime integrity implementation is missing')
        self.integrity = importlib.import_module('b300_core.runtime_integrity')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, data in {'app.exe': b'app', '_internal/python.dll': b'python',
                           'vendor/openocd/tool': b'openocd',
                           'resources/firmware/image.bin': b'firmware'}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def manifest(self):
        return self.integrity.write_runtime_manifest(self.root, __version__)

    def test_manifest_covers_complete_tree_deterministically_and_excludes_itself(self):
        target = self.manifest()
        first = target.read_bytes()
        expected = '# B300 runtime ' + __version__ + '\n'
        for name, data in sorted({'app.exe': b'app', '_internal/python.dll': b'python',
                                 'vendor/openocd/tool': b'openocd',
                                 'resources/firmware/image.bin': b'firmware'}.items()):
            expected += hashlib.sha256(data).hexdigest() + ' *' + name + '\n'
        self.assertEqual(first, expected.encode())
        self.manifest()
        self.assertEqual(target.read_bytes(), first)
        self.integrity.validate_runtime(self.root, __version__)
        (self.root / 'user-notes.txt').write_text('retained')
        self.integrity.validate_runtime(self.root, __version__)

    def test_corrupt_or_missing_required_file_is_rejected(self):
        for name in ('app.exe', '_internal/python.dll', 'vendor/openocd/tool',
                     'resources/firmware/image.bin'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                self.manifest()
                path.write_bytes(b'corrupt')
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.integrity.validate_runtime(self.root, __version__)
                path.unlink()
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.integrity.validate_runtime(self.root, __version__)
                path.write_bytes(original)

    def test_missing_malformed_version_empty_duplicate_and_unsafe_manifest_rejected(self):
        with self.assertRaises(self.integrity.RuntimeIntegrityError):
            self.integrity.validate_runtime(self.root, __version__)
        target = self.manifest()
        valid = target.read_text()
        digest = hashlib.sha256(b'app').hexdigest()
        for text in ('', '# B300 runtime wrong\n' + digest + ' *app.exe\n',
                     '# B300 runtime ' + __version__ + '\n',
                     valid + digest + ' *app.exe\n',
                     valid.replace(digest, 'z' * 64)):
            with self.subTest(text=text):
                target.write_text(text)
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.integrity.validate_runtime(self.root, __version__)
        for name in ('../app.exe', '/app.exe', 'C:/app.exe', 'a/../app.exe',
                     'a\\app.exe', 'a//app.exe', './app.exe', 'app.exe:stream',
                     'B300-RUNTIME.sha256', 'APP.EXE', 'app.exe.', 'NUL'):
            with self.subTest(name=name):
                target.write_text(valid + digest + ' *' + name + '\n')
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.integrity.validate_runtime(self.root, __version__)

    def test_unreadable_subtree_cannot_silently_disappear_from_manifest(self):
        original = os.scandir

        def denied(path):
            if Path(path).name == '_internal':
                raise PermissionError('fixture unreadable runtime directory')
            return original(path)

        with mock.patch('os.scandir', side_effect=denied):
            with self.assertRaises(self.integrity.RuntimeIntegrityError):
                self.manifest()

    @unittest.skipUnless(os.name == 'nt', 'Windows junction safety')
    def test_directory_junction_cannot_escape_manifest_root(self):
        self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory)
            (external / 'python.dll').write_bytes(b'python')
            internal = self.root / '_internal'
            (internal / 'python.dll').unlink()
            internal.rmdir()
            env = os.environ.copy()
            env['B300_INTEGRITY_LINK'] = str(internal)
            env['B300_INTEGRITY_TARGET'] = str(external)
            result = subprocess.run([
                'powershell', '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                '-Path $env:B300_INTEGRITY_LINK -Target $env:B300_INTEGRITY_TARGET | Out-Null',
            ], env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.integrity.validate_runtime(self.root, __version__)
                with self.assertRaises(self.integrity.RuntimeIntegrityError):
                    self.manifest()
                self.assertEqual((external / 'python.dll').read_bytes(), b'python')
            finally:
                internal.rmdir()

    def test_symlink_manifest_payload_or_directory_is_rejected(self):
        self.manifest()
        outside = self.root.parent / (self.root.name + '-outside')
        outside.write_bytes(b'app')
        self.addCleanup(outside.unlink)
        payload = self.root / 'app.exe'
        payload.unlink()
        try:
            payload.symlink_to(outside)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        with self.assertRaises(self.integrity.RuntimeIntegrityError):
            self.integrity.validate_runtime(self.root, __version__)
        with self.assertRaises(self.integrity.RuntimeIntegrityError):
            self.manifest()


class RuntimePackageTests(unittest.TestCase):
    def test_zip_and_tar_manifest_cover_all_archived_payload_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            (app / '_internal').mkdir(parents=True)
            exe = app / 'app.exe'
            exe.write_bytes(b'app')
            (app / '_internal/python.dll').write_bytes(b'python')
            # A stale manifest must be regenerated from the staged complete tree.
            (app / 'B300-RUNTIME.sha256').write_bytes(b'stale')
            openocd = root / 'openocd'
            (openocd / 'bin').mkdir(parents=True)
            (openocd / 'bin/openocd').write_bytes(b'openocd')
            bootstrap = root / 'bootstrap.sh'
            bootstrap.write_bytes(b'bootstrap')
            package = root / 'openocd.tar.gz'
            package.write_bytes(b'package')
            resource = root / 'resources/firmware/image.bin'
            resource.parent.mkdir(parents=True)
            resource.write_bytes(b'firmware')
            digest = hashlib.sha256(package_internal.openocd_manifest(openocd)).hexdigest()
            for suffix in ('.zip', '.tar.gz'):
                with self.subTest(suffix=suffix):
                    output = root / ('bundle' + suffix)
                    with mock.patch.dict(package_internal.TRUSTED_TREE_MANIFESTS, {'linux-x64': digest}):
                        package_internal.main([
                            '--flavor', 'gui', '--executable', str(exe),
                            '--application-root', str(app), '--resource', str(resource),
                            '--openocd-root', str(openocd), '--bootstrap', str(bootstrap),
                            '--output', str(output), '--platform', 'linux-x64',
                            '--version', __version__, '--openocd-archive', 'openocd.tar.gz',
                            '--openocd-sha256', 'a' * 64, '--openocd-package', str(package),
                            '--internal-distribution-approved'])
                    if suffix == '.zip':
                        with zipfile.ZipFile(output) as archive:
                            contents = {name: archive.read(name) for name in archive.namelist()}
                    else:
                        with tarfile.open(output) as archive:
                            contents = {item.name: archive.extractfile(item).read()
                                        for item in archive if item.isfile()}
                    self.assertIn('B300-RUNTIME.sha256', contents,
                                  'Every runtime package needs a complete integrity manifest')
                    manifest = contents.pop('B300-RUNTIME.sha256').decode()
                    lines = manifest.splitlines()
                    self.assertEqual(lines.pop(0), '# B300 runtime ' + __version__)
                    expected = [hashlib.sha256(data).hexdigest() + ' *' + name
                                for name, data in sorted(contents.items())]
                    self.assertEqual(lines, expected)
                    self.assertIn('_internal/python.dll', contents)
                    self.assertIn('resources/firmware/image.bin', contents)


if __name__ == '__main__':
    unittest.main()
