from __future__ import annotations

import json
import hashlib
import gzip
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.gateway_system_mode import ingress_mount_isolated, parse_isolated_gateway_record
from b300_core.gateway_unix_transport import GatewayUnixServer


INGRESS = Path('/var/spool/b300-stlink/ingress')


class IngressMountTests(unittest.TestCase):
    def test_exact_bounded_tmpfs_is_accepted(self):
        record = {'filesystems': [{'target': str(INGRESS), 'fstype': 'tmpfs',
                  'size': 68157440, 'options':
                  'rw,nodev,nosuid,noexec,size=66560k,nr_inodes=256,uid=2001,gid=2002,mode=710'}]}
        seen = []

        def run(command):
            seen.append(tuple(command))
            return SimpleNamespace(returncode=0, stdout=json.dumps(record))

        self.assertTrue(ingress_mount_isolated(INGRESS, uid=2001, gid=2002, runner=run))
        self.assertIn('--mountpoint', seen[0])
        self.assertEqual(seen[0][-1], str(INGRESS))

    def test_missing_or_wrong_mount_fails_closed(self):
        valid = {'target': str(INGRESS), 'fstype': 'tmpfs', 'size': 68157440,
                 'options': 'rw,nodev,nosuid,noexec,nr_inodes=256,uid=2001,gid=2002,mode=710'}
        cases = [
            (1, ''),
            (0, json.dumps({'filesystems': []})),
            (0, json.dumps({'filesystems': [{**valid, 'fstype': 'ext4'}]})),
            (0, json.dumps({'filesystems': [{**valid, 'target': '/var/spool'}]})),
            (0, json.dumps({'filesystems': [{**valid, 'size': 134217728}]})),
            (0, json.dumps({'filesystems': [{**valid, 'options': valid['options'].replace('nr_inodes=256', 'nr_inodes=512')}]})),
            (0, json.dumps({'filesystems': [{**valid, 'options': valid['options'].replace('nodev,', '')}]})),
            (0, '{broken'),
        ]
        for code, output in cases:
            with self.subTest(code=code, output=output):
                run = lambda command: SimpleNamespace(returncode=code, stdout=output)
                self.assertFalse(ingress_mount_isolated(INGRESS, uid=2001, gid=2002, runner=run))


class LegacyMarkerTests(unittest.TestCase):
    def test_current_marker_preserves_explicit_active_state(self):
        current = {'schema_version': 1, 'socket_path': '/run/b300-stlink/agent.sock',
                   'state_root': '/var/lib/b300-stlink/gateway',
                   'ingress_root': '/var/spool/b300-stlink/ingress',
                   'operator_uid': 1000, 'operator_gid': 2002,
                   'flash_enabled': True}
        config = parse_isolated_gateway_record(current)
        self.assertTrue(config.flash_enabled)
        self.assertEqual(config.operator_gid, 2002)

    def test_original_marker_schema_loads_as_pending_without_group_guess(self):
        old = {'schema_version': 1, 'socket_path': '/run/b300-stlink/agent.sock',
               'state_root': '/var/lib/b300-stlink/gateway',
               'ingress_root': '/var/spool/b300-stlink/ingress',
               'operator_uid': 1000}
        config = parse_isolated_gateway_record(old)
        self.assertFalse(config.flash_enabled)
        self.assertIsNone(config.operator_gid)
        GatewayUnixServer(config.socket_path, config.operator_uid,
                          lambda _request, _timeout: {}, allowed_gid=config.operator_gid)

    def test_malformed_legacy_marker_does_not_get_pending_exemption(self):
        old = {'schema_version': 1, 'socket_path': '/tmp/agent.sock',
               'state_root': '/var/lib/b300-stlink/gateway',
               'ingress_root': '/var/spool/b300-stlink/ingress',
               'operator_uid': 1000}
        with self.assertRaises(ValueError):
            parse_isolated_gateway_record(old)


class InstallerPlanTests(unittest.TestCase):
    def test_script_is_directly_invocable_from_repository_root(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/install_isolated_gateway.py'
        result = subprocess.run((sys.executable, str(source), '--help'),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('plan', result.stdout)

    def test_script_help_works_without_source_tree(self):
        source = Path(__file__).resolve().parents[1] / 'scripts/install_isolated_gateway.py'
        isolated = self.root / 'isolated-installer.py'
        shutil.copy2(source, isolated)
        result = subprocess.run((sys.executable, '-I', str(isolated), '--help'),
                                cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('plan', result.stdout)

    def setUp(self):
        from scripts import install_isolated_gateway as installer
        self.installer = installer
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'candidate.tar.gz'
        with tarfile.open(self.bundle, 'w:gz') as archive:
            for name, data in {
                'BUNDLE-METADATA.txt': b'platform=linux-x64\nflavor=cli\nversion=0.23.0\n',
                'B300-RUNTIME.sha256': b'# B300 runtime 0.23.0\n',
                'b300-stlink': b'binary',
                'packaging/linux/b300-stlink-gateway-agent-system.service': b'unit',
                'packaging/linux/b300-stlink-ingress.mount.in': b'mount',
            }.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        self.evidence = {
            'os_name': 'linux', 'distribution': 'ubuntu',
            'machine': 'x86_64', 'operator_uid': 1000,
            'operator_home': '/home/aubot',
            'services': {'legacy_user': {'enabled': True, 'active': True},
                         'system_agent': {'enabled': False, 'active': False},
                         'ingress_mount': {'enabled': False, 'active': False}},
            'owner_states': {'legacy': 'IDLE', 'system': 'MISSING'},
            'lease_present': {'legacy': False, 'system': False},
            'jobs': {'legacy': {'SUCCEEDED': 2}, 'system': {}},
            'openocd_quiescent': True,
            'probe': {'count': 1, 'incomplete_count': 0, 'selected': True,
                      'node': '/dev/bus/usb/001/002',
                      'uid': 0, 'gid': 46, 'mode': '0660', 'agent_owned': False,
                      'acl_known': True},
            'groups': {'b300-agent': {'exists': False},
                       'b300-upload': {'exists': False},
                       'b300-operator': {'exists': False},
                       'plugdev': {'exists': True, 'gid': 46, 'operator_member': True}},
            'files': {'legacy_bundle_manifest': {'exists': True, 'sha256': 'a' * 64},
                      'system_unit': {'exists': False},
                      'mount_unit': {'exists': False},
                      'agent_udev_rule': {'exists': False},
                      'legacy_udev_rule': {'exists': True, 'sha256': 'c' * 64},
                      'vendor_udev_rule': {'exists': True, 'sha256': 'b' * 64}},
            'path_hazards': (),
        }

    def _stat(self, path):
        selected = Path(path)
        mode = (stat.S_IFREG | 0o600) if selected == self.bundle else (stat.S_IFDIR | 0o700)
        return SimpleNamespace(st_mode=mode, st_uid=0, st_size=self.bundle.stat().st_size)

    def _host(self, evidence=None):
        class FakeHost:
            def __init__(self, record):
                self.record = record
                self.reads = 0
                self.mutations = []

            def inspect(self, probe_serial=None):
                self.reads += 1
                return self.record

        return FakeHost(self.evidence if evidence is None else evidence)

    def _plan(self, evidence=None, **kwargs):
        host = self._host(evidence)
        plan = self.installer.build_plan(
            self.bundle, self.digest, host=host, trust_root=self.root,
            path_stat=kwargs.pop('path_stat', self._stat), **kwargs)
        return plan, host

    def test_plan_is_read_only_and_terminal_evidence_is_rollback_inventory(self):
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('*'))
        plan, host = self._plan()
        record = plan.to_record()
        self.assertTrue(record['ready'])
        self.assertEqual(record['blockers'], [])
        self.assertEqual(record['candidate']['sha256'], self.digest)
        self.assertEqual(record['candidate']['platform'], 'linux-x64')
        self.assertEqual(record['rollback_inventory']['jobs']['legacy'], {'SUCCEEDED': 2})
        self.assertTrue(record['rollback_inventory']['services']['legacy_user']['enabled'])
        self.assertEqual(record['rollback_inventory']['files']['legacy_udev_rule']['sha256'],
                         'c' * 64)
        self.assertEqual(host.reads, 1)
        self.assertEqual(host.mutations, [])
        self.assertEqual(before, sorted(path.relative_to(self.root).as_posix()
                                        for path in self.root.rglob('*')))
        self.assertNotIn('approval_token', json.dumps(record))

    def test_bad_hash_and_symlink_path_block_before_any_apply(self):
        wrong = '0' * 64
        host = self._host()
        plan = self.installer.build_plan(self.bundle, wrong, host=host,
                                         trust_root=self.root, path_stat=self._stat)
        self.assertIn('BUNDLE_HASH_MISMATCH',
                      {item['code'] for item in plan.to_record()['blockers']})

        def symlink_stat(path):
            info = self._stat(path)
            if Path(path) == self.bundle:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777,
                                       st_uid=0, st_size=info.st_size)
            return info

        plan = self.installer.build_plan(self.bundle, self.digest, host=self._host(),
                                         trust_root=self.root, path_stat=symlink_stat)
        self.assertIn('BUNDLE_PATH_UNSAFE',
                      {item['code'] for item in plan.to_record()['blockers']})

    def test_architecture_and_active_evidence_are_explicit_blockers(self):
        evidence = {**self.evidence, 'machine': 'aarch64',
                    'lease_present': {'legacy': True, 'system': False},
                    'jobs': {'legacy': {'RUNNING': 1, 'SUCCEEDED': 2}, 'system': {}},
                    'openocd_quiescent': False}
        plan, _ = self._plan(evidence)
        codes = {item['code'] for item in plan.to_record()['blockers']}
        self.assertTrue({'BUNDLE_ARCH_MISMATCH', 'LEASE_PRESENT',
                         'ACTIVE_JOBS', 'OPENOCD_NOT_QUIESCENT'}.issubset(codes))

    def test_non_ubuntu_linux_is_no_go(self):
        evidence = {**self.evidence, 'distribution': 'debian'}
        plan, _ = self._plan(evidence)
        self.assertIn('HOST_NOT_UBUNTU',
                      {item['code'] for item in plan.to_record()['blockers']})

    def test_unknown_job_state_cannot_be_filtered_into_go(self):
        evidence = {**self.evidence,
                    'jobs': {'legacy': {'UNKNOWN_STATE': 1}, 'system': {}}}
        plan, _ = self._plan(evidence)
        self.assertIn('ACTIVE_JOBS',
                      {item['code'] for item in plan.to_record()['blockers']})

    def test_untrusted_probe_field_cannot_echo_secret_as_hash(self):
        files = {**self.evidence['files'],
                 'legacy_bundle_manifest': {'exists': True,
                                            'sha256': 'SECRET-NOT-OUTPUT'}}
        plan, _ = self._plan({**self.evidence, 'files': files})
        self.assertNotIn('SECRET-NOT-OUTPUT', json.dumps(plan.to_record()))

    def test_cli_plan_json_never_invokes_mutating_host_method(self):
        output = io.StringIO()
        host = self._host()
        code = self.installer.main(
            ['plan', '--json', '--bundle', str(self.bundle),
             '--expected-sha256', self.digest],
            host=host, trust_root=self.root, path_stat=self._stat, output=output)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ready'])
        self.assertEqual(host.mutations, [])

    def test_apply_is_not_an_implemented_subcommand(self):
        host = self._host()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.installer.main(['apply', '--confirm-system-change'], host=host,
                                output=io.StringIO())
        self.assertEqual(host.reads, 0)

    def test_job_inventory_counts_states_without_returning_secret_fields(self):
        jobs = self.root / 'program-jobs'
        jobs.mkdir()
        for index, state in enumerate(('SUCCEEDED', 'RUNNING')):
            job_id = format(index + 1, '032x')
            directory = jobs / job_id
            directory.mkdir()
            (directory / 'job.json').write_text(json.dumps({
                'job_id': job_id, 'state': state, 'approval_token': 'SECRET-NOT-OUTPUT',
            }), encoding='utf-8')
        counts = self.installer.inspect_job_states(jobs)
        self.assertEqual(counts, {'SUCCEEDED': 1, 'RUNNING': 1})
        self.assertNotIn('SECRET-NOT-OUTPUT', json.dumps(counts))

    def test_linux_host_probe_uses_only_readonly_queries(self):
        sysfs = self.root / 'sys/bus/usb/devices/1-1'
        sysfs.mkdir(parents=True)
        for name, value in {'idVendor': '0483', 'idProduct': '3748',
                            'busnum': '1', 'devnum': '2', 'serial': 'SAFE123'}.items():
            (sysfs / name).write_text(value, encoding='ascii')
        node = self.root / 'dev/bus/usb/001/002'
        node.parent.mkdir(parents=True)
        node.write_bytes(b'')
        os_release = self.root / 'etc/os-release'
        os_release.parent.mkdir(parents=True)
        os_release.write_text('ID=ubuntu\n', encoding='utf-8')
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob('*'))
        commands = []

        def runner(command):
            commands.append(tuple(command))
            if command[0] == 'getfacl':
                return SimpleNamespace(returncode=0, stdout='user:aubot:rw-\n')
            self.assertEqual(command[0], 'systemctl')
            self.assertIn(command[-2], ('is-enabled', 'is-active'))
            value = ('enabled' if command[-2] == 'is-enabled' else 'active')
            if '--user' not in command:
                value = ('disabled' if command[-2] == 'is-enabled' else 'inactive')
            return SimpleNamespace(returncode=0, stdout=value + '\n')

        def group(name):
            if name == 'plugdev':
                return SimpleNamespace(gr_gid=46, gr_mem=['aubot'])
            raise KeyError(name)

        probe = self.installer.LinuxHostProbe(
            root=self.root, runner=runner, system_name='Linux', machine='x86_64',
            account_lookup=lambda name: SimpleNamespace(
                pw_uid=1000, pw_gid=1000, pw_dir='/home/aubot'),
            group_lookup=group, quiescent_probe=lambda: True)
        evidence = probe.inspect()
        self.assertEqual(evidence['distribution'], 'ubuntu')
        self.assertEqual(evidence['probe']['count'], 1)
        self.assertTrue(evidence['probe']['acl_known'])
        self.assertTrue(evidence['probe']['operator_acl'])
        self.assertTrue(evidence['services']['legacy_user']['enabled'])
        self.assertEqual(evidence['jobs']['legacy'], {})
        self.assertTrue(commands)
        with self.assertRaises(ValueError):
            probe._query(('systemctl', 'start', 'unsafe.service',
                          'is-active', 'b300-stlink-gateway-agent.service'))
        self.assertEqual(before, sorted(path.relative_to(self.root).as_posix()
                                        for path in self.root.rglob('*')))

    def test_incomplete_second_stlink_is_counted_and_blocks_pinned_selection(self):
        for name, complete in (('1-1', True), ('1-2', False)):
            device = self.root / 'sys/bus/usb/devices' / name
            device.mkdir(parents=True)
            (device / 'idVendor').write_text('0483', encoding='ascii')
            (device / 'idProduct').write_text('3748', encoding='ascii')
            if complete:
                for field, value in {'serial': 'SAFE123', 'busnum': '1',
                                     'devnum': '2'}.items():
                    (device / field).write_text(value, encoding='ascii')
        node = self.root / 'dev/bus/usb/001/002'
        node.parent.mkdir(parents=True)
        node.write_bytes(b'')
        probe = self.installer.LinuxHostProbe(
            root=self.root, system_name='Linux',
            runner=lambda command: SimpleNamespace(returncode=0, stdout='user:aubot:rw-'))
        evidence = probe._probe_state('SAFE123', {'exists': False})
        self.assertEqual(evidence['count'], 2)
        self.assertEqual(evidence['incomplete_count'], 1)
        self.assertFalse(evidence['selected'])
        plan, _ = self._plan({**self.evidence, 'probe': evidence}, probe_serial='SAFE123')
        codes = {item['code'] for item in plan.to_record()['blockers']}
        self.assertIn('PROBE_INCOMPLETE', codes)
        self.assertIn('PROBE_NOT_UNIQUE', codes)

    def test_declared_oversized_tar_member_is_rejected_from_header(self):
        member = tarfile.TarInfo('oversized.bin')
        member.size = 1024 * 1024 * 1024
        with gzip.open(self.bundle, 'wb') as stream:
            stream.write(member.tobuf())
            stream.write(b'\0' * 1024)
        with self.assertRaisesRegex(ValueError, 'member exceeds'):
            self.installer._inspect_bundle(self.bundle)

    def test_tar_member_count_and_total_declared_size_are_bounded(self):
        with mock.patch.object(self.installer, 'MAX_ARCHIVE_MEMBERS', 1):
            with self.assertRaisesRegex(ValueError, 'too many members'):
                self.installer._inspect_bundle(self.bundle)
        with mock.patch.object(self.installer, 'MAX_EXPANDED_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'expanded size exceeds'):
                self.installer._inspect_bundle(self.bundle)

    def test_system_target_owned_by_operator_is_path_blocker(self):
        probe = self.installer.LinuxHostProbe(root=self.root, system_name='Linux')
        foreign = self.root / 'opt/b300-stlink'

        def path_info(path):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700,
                                   st_uid=1000 if Path(path) == foreign else 0)

        with mock.patch.object(Path, 'lstat', autospec=True, side_effect=path_info):
            hazards = probe._path_hazards()
        self.assertIn('/opt/b300-stlink', hazards)

    def test_owner_idle_record_requires_exact_schema(self):
        path = self.root / 'var/lib/b300-stlink/gateway/hardware-owner.lock'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\0{"schema_version":1,"state":"IDLE","extra":"unsafe"}')
        probe = self.installer.LinuxHostProbe(root=self.root, system_name='Linux')
        self.assertEqual(probe._owner_state('/var/lib/b300-stlink/gateway/hardware-owner.lock'),
                         'CORRUPT')

    def test_ubuntu_os_release_symlink_to_usr_lib_is_accepted(self):
        target = self.root / 'usr/lib/os-release'
        target.parent.mkdir(parents=True)
        target.write_text('ID=ubuntu\n', encoding='utf-8')
        link = self.root / 'etc/os-release'
        link.parent.mkdir(parents=True)
        actual_lstat = Path.lstat

        def path_info(path):
            if Path(path) == link:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0)
            return actual_lstat(path)

        probe = self.installer.LinuxHostProbe(root=self.root, system_name='Linux')
        with mock.patch.object(Path, 'lstat', autospec=True, side_effect=path_info), \
             mock.patch.object(os, 'readlink', return_value='../usr/lib/os-release'):
            self.assertEqual(probe._distribution(), 'ubuntu')


class MarkerTransitionTests(unittest.TestCase):
    def setUp(self):
        from scripts import install_isolated_gateway as installer
        self.installer = installer
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.test_uid = os.getuid() if hasattr(os, 'getuid') else 0
        root = Path(self.temp.name)
        self.marker = root / 'etc/b300-stlink/isolated-gateway.json'
        self.marker.parent.mkdir(parents=True)
        self.lock = root / 'var/lib/b300-stlink/gateway/hardware-owner.lock'
        self.lock.parent.mkdir(parents=True)
        self.marker.write_text(json.dumps({
            'schema_version': 1,
            'socket_path': '/run/b300-stlink/agent.sock',
            'state_root': '/var/lib/b300-stlink/gateway',
            'ingress_root': '/var/spool/b300-stlink/ingress',
            'operator_uid': 1000, 'operator_gid': 2002,
            'flash_enabled': True,
        }), encoding='utf-8')
        self.lock.write_bytes(b'\0{"schema_version":1,"state":"IDLE"}')
        self.events = []

        class Locker:
            held = False

            def acquire(inner, fd):
                self.events.append(('acquire', os.fstat(fd).st_ino))
                if inner.held:
                    raise BlockingIOError('worker owns lock')
                inner.held = True

            def release(inner, fd):
                self.events.append(('release', os.fstat(fd).st_ino))
                inner.held = False

        self.locker = Locker()

    def _safe_stat(self, path):
        raw = os.lstat(path)
        mode = stat.S_IFDIR | 0o700 if stat.S_ISDIR(raw.st_mode) else stat.S_IFREG | 0o600
        return SimpleNamespace(st_mode=mode, st_uid=self.test_uid, st_dev=raw.st_dev,
                               st_ino=raw.st_ino, st_nlink=raw.st_nlink,
                               st_size=raw.st_size)

    def _safe_fstat(self, fd):
        raw = os.fstat(fd)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=self.test_uid,
                               st_dev=raw.st_dev, st_ino=raw.st_ino,
                               st_nlink=raw.st_nlink, st_size=raw.st_size)

    def _probes(self, *, agent=True, jobs=True, openocd=True):
        return SimpleNamespace(
            agent_idle=lambda: self.events.append('agent') or agent,
            jobs_idle=lambda: self.events.append('jobs') or jobs,
            openocd_quiescent=lambda: self.events.append('openocd') or openocd)

    def _transition(self, probes=None, **options):
        return self.installer._transition_active_to_pending(
            self.marker, self.lock, probes=probes or self._probes(),
            locker=options.pop('locker', self.locker),
            effective_uid=lambda: 0, system_name='linux', trusted_uid=self.test_uid,
            path_stat=options.pop('path_stat', self._safe_stat),
            fd_stat=self._safe_fstat,
            fsync_dir=lambda path: self.events.append('fsync_dir'),
            timeout_seconds=options.pop('timeout_seconds', 0.02),
            **options)

    def test_worker_owned_lock_times_out_without_marker_mutation(self):
        self.locker.held = True
        original = self.marker.read_bytes()
        inode = self.lock.stat().st_ino
        with self.assertRaises(Exception) as captured:
            self._transition(timeout_seconds=0.01)
        self.assertEqual(captured.exception.reason_code, 'LOCK_BUSY')
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual(self.lock.stat().st_ino, inode)
        self.assertNotIn('agent', self.events)

    def test_wrong_trusted_uid_refuses_without_marker_mutation(self):
        original = self.marker.read_bytes()
        with self.assertRaises(Exception) as captured:
            self.installer._transition_active_to_pending(
                self.marker, self.lock, probes=self._probes(), locker=self.locker,
                effective_uid=lambda: 0, system_name='linux',
                trusted_uid=self.test_uid + 1, path_stat=self._safe_stat,
                fd_stat=self._safe_fstat, fsync_dir=lambda path: None)
        self.assertEqual(captured.exception.reason_code, 'PATH_UNSAFE')
        self.assertEqual(self.marker.read_bytes(), original)

    def test_public_transition_requires_root_linux(self):
        with mock.patch.object(sys, 'platform', 'linux'), \
             mock.patch.object(os, 'geteuid', return_value=1000, create=True):
            with self.assertRaises(Exception) as captured:
                self.installer.transition_active_to_pending(probes=self._probes())
        self.assertEqual(captured.exception.reason_code, 'ROOT_LINUX_REQUIRED')

    def test_transition_wins_and_fsyncs_pending_before_unlock(self):
        from b300_core.gateway_program_jobs import GatewayProgramJobs, ProgramJobError
        from b300_core.gateway_system_mode import parse_isolated_gateway_record
        original_inode = self.lock.stat().st_ino
        result = self._transition()
        self.assertEqual(result['state'], 'PENDING_REPLUG')
        pending = parse_isolated_gateway_record(
            json.loads(self.marker.read_text(encoding='utf-8')))
        self.assertFalse(pending.flash_enabled)
        worker_jobs = GatewayProgramJobs.__new__(GatewayProgramJobs)
        worker_jobs.ingress_root = pending.ingress_root
        with mock.patch('b300_core.gateway_program_jobs.load_isolated_gateway_config',
                        return_value=pending):
            with self.assertRaises(ProgramJobError) as blocked:
                worker_jobs._require_isolated_programming()
        self.assertEqual(blocked.exception.reason_code, 'ISOLATED_FLASH_DISABLED')
        self.assertEqual(self.lock.stat().st_ino, original_inode)
        self.assertEqual(self.lock.read_bytes(), b'\0{"schema_version":1,"state":"IDLE"}')
        self.assertLess(self.events.index('fsync_dir'),
                        next(i for i, event in enumerate(self.events)
                             if isinstance(event, tuple) and event[0] == 'release'))
        self.assertEqual([event for event in self.events if isinstance(event, str)],
                         ['agent', 'jobs', 'openocd', 'fsync_dir'])

    def test_crash_released_flock_with_active_record_refuses(self):
        self.lock.write_bytes(b'\0{"schema_version":1,"state":"ACTIVE",'
                              b'"pid":123,"instance_id":"' + b'a' * 32 + b'"}')
        original = self.marker.read_bytes()
        with self.assertRaises(Exception) as captured:
            self._transition()
        self.assertEqual(captured.exception.reason_code, 'OWNER_NOT_IDLE')
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertNotIn('agent', self.events)

    def test_path_replacement_after_flock_refuses_without_marker_change(self):
        original = self.marker.read_bytes()
        calls = [0]

        def changed_stat(path):
            info = self._safe_stat(path)
            if Path(path) == self.lock:
                calls[0] += 1
                if calls[0] > 1:
                    return SimpleNamespace(**{**vars(info), 'st_ino': info.st_ino + 1})
            return info

        with self.assertRaises(Exception) as captured:
            self._transition(path_stat=changed_stat)
        self.assertEqual(captured.exception.reason_code, 'LOCK_REPLACED')
        self.assertEqual(self.marker.read_bytes(), original)

    def test_uncertain_job_probe_blocks_before_marker_write(self):
        original = self.marker.read_bytes()
        for unavailable in ('agent', 'jobs', 'openocd'):
            with self.subTest(unavailable=unavailable):
                self.events.clear()
                probes = self._probes(**{unavailable: False})
                with self.assertRaises(Exception) as captured:
                    self._transition(probes=probes)
                self.assertEqual(captured.exception.reason_code, 'QUIESCENCE_UNKNOWN')
                self.assertEqual(self.marker.read_bytes(), original)
                self.assertNotIn('fsync_dir', self.events)

    def test_missing_or_corrupt_durable_owner_record_refuses(self):
        original_marker = self.marker.read_bytes()
        for content in (b'\0', b'\0{}', b'not-a-lock-record'):
            with self.subTest(content=content):
                self.lock.write_bytes(content)
                with self.assertRaises(Exception) as captured:
                    self._transition()
                self.assertEqual(captured.exception.reason_code, 'OWNER_RECORD_INVALID')
                self.assertEqual(self.marker.read_bytes(), original_marker)
                self.assertNotIn('fsync_dir', self.events)
        self.lock.unlink()
        with self.assertRaises(Exception) as captured:
            self._transition()
        self.assertEqual(captured.exception.reason_code, 'LOCK_MISSING')
        self.assertEqual(self.marker.read_bytes(), original_marker)

    def test_adopted_flash_keeps_owner_through_disconnect_and_shutdown(self):
        from b300_core.gateway_lease import GatewayLeaseStore
        from b300_core.gateway_lease_coordinator import GatewayLeaseCoordinator
        from b300_core.hardware_owner import FileHardwareOwner
        from b300_core.models import ProbeInfo
        from tests.test_gateway_lease_coordinator import FakeSupervisor, request

        coordinator = GatewayLeaseCoordinator(
            FakeSupervisor(), store=GatewayLeaseStore(self.lock.parent / 'lease.json'),
            hardware_owner=FileHardwareOwner(self.lock),
            probe_discovery=lambda: (ProbeInfo('SAFE123', 'ST-Link', 'test', 'usb:1'),))
        grant = coordinator.acquire(request('client-flash', 'FLASH_APPLICATION'))
        coordinator.adopt_flash_job(grant.lease_id, grant.token, grant.generation)
        self.assertTrue(coordinator.release(grant.lease_id, grant.token, grant.generation).active)
        self.assertTrue(coordinator.shutdown().active)

        base_locker = self.locker

        class CoupledLocker:
            def acquire(self, fd):
                if coordinator.owns_flash_lease(grant.lease_id, grant.token, grant.generation):
                    raise BlockingIOError('adopted job still owns hardware')
                base_locker.acquire(fd)

            def release(self, fd):
                base_locker.release(fd)

        original = self.marker.read_bytes()
        with self.assertRaises(Exception) as captured:
            self._transition(locker=CoupledLocker(), timeout_seconds=0.01)
        self.assertEqual(captured.exception.reason_code, 'LOCK_BUSY')
        self.assertEqual(self.marker.read_bytes(), original)

        self.assertFalse(coordinator.finish_flash_job(
            grant.lease_id, grant.token, grant.generation).active)
        self.assertEqual(self._transition(locker=CoupledLocker())['state'], 'PENDING_REPLUG')

    @unittest.skipUnless(os.name == 'posix', 'Linux flock integration required')
    def test_native_flock_contends_with_file_hardware_owner_inode(self):
        from b300_core.hardware_owner import FileHardwareOwner
        owner = FileHardwareOwner(self.lock)
        token = owner.acquire()
        try:
            with self.assertRaises(Exception) as captured:
                self._transition(locker=self.installer._LinuxFlock(),
                                 timeout_seconds=0.01)
            self.assertEqual(captured.exception.reason_code, 'LOCK_BUSY')
            self.assertTrue(json.loads(self.marker.read_text(encoding='utf-8'))['flash_enabled'])
        finally:
            token.release()
        self.assertEqual(self._transition(locker=self.installer._LinuxFlock())['state'],
                         'PENDING_REPLUG')


class InstallerStageTests(unittest.TestCase):
    def setUp(self):
        from scripts import install_isolated_gateway as installer
        self.installer = installer
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opt = self.root / 'opt'
        self.opt.mkdir()
        self.install_root = self.opt / 'b300-stlink'
        self.bundle = self.root / 'candidate.tar.gz'
        self.calls = []
        self._write_bundle()

    def _write_bundle(self, extra=None):
        repo = Path(__file__).resolve().parents[1]
        files = {
            'BUNDLE-METADATA.txt': b'platform=linux-x64\nflavor=cli\nversion=0.23.0\n',
            'b300-stlink': b'portable-cli',
            'packaging/linux/b300-stlink-gateway-agent-system.service': (
                repo / 'packaging/linux/b300-stlink-gateway-agent-system.service').read_bytes(),
            'packaging/linux/b300-stlink-ingress.mount.in': (
                repo / 'packaging/linux/b300-stlink-ingress.mount.in').read_bytes(),
        }
        files.update(extra or {})
        lines = ['# B300 runtime 0.23.0']
        for name, data in sorted(files.items()):
            lines.append(hashlib.sha256(data).hexdigest() + ' *' + name)
        files['B300-RUNTIME.sha256'] = ('\n'.join(lines) + '\n').encode('utf-8')
        with tarfile.open(self.bundle, 'w:gz') as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name == 'b300-stlink' else 0o644
                archive.addfile(member, io.BytesIO(data))
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()

    @staticmethod
    def _trusted_stat(path):
        raw = os.lstat(path)
        mode = (stat.S_IFDIR | 0o700) if stat.S_ISDIR(raw.st_mode) else (stat.S_IFREG | 0o600)
        return SimpleNamespace(st_mode=mode, st_uid=0, st_dev=raw.st_dev,
                               st_ino=raw.st_ino, st_nlink=raw.st_nlink,
                               st_size=raw.st_size)

    @staticmethod
    def _trusted_fstat(fd):
        raw = os.fstat(fd)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0,
                               st_dev=raw.st_dev, st_ino=raw.st_ino,
                               st_nlink=raw.st_nlink, st_size=raw.st_size)

    def _host(self):
        base = InstallerPlanTests(methodName='test_plan_is_read_only_and_terminal_evidence_is_rollback_inventory')
        base.setUp()
        self.addCleanup(base.doCleanups)
        class Host:
            def inspect(inner, probe_serial=None):
                self.calls.append('inspect')
                return base.evidence
        return Host()

    def _stage(self, *, fsync_dir=None, host=None, plan=None,
               path_stat=None, promote=None):
        selected_host = host or self._host()
        selected_stat = path_stat or self._trusted_stat
        selected_plan = plan or self.installer.build_plan(
            self.bundle, self.digest, host=selected_host, trust_root=self.root,
            path_stat=selected_stat)
        return self.installer.stage_candidate(
            selected_plan, self.bundle, self.digest, host=selected_host,
            install_root=self.install_root, trust_root=self.root,
            path_stat=selected_stat, fd_stat=self._trusted_fstat,
            trusted_uid=0,
            effective_uid=lambda: 0, system_name='linux',
            identity_provider=lambda: self.calls.append('id') or (2001, 2002),
            escape_unit=lambda path: self.calls.append('escape') or
                        r'var-spool-b300\x2dstlink-ingress.mount',
            fsync_dir=fsync_dir or (lambda path: self.calls.append('fsync_dir')),
            promote=promote)

    def test_stage_writes_journal_before_candidate_and_renders_units_only_in_candidate(self):
        result = self._stage()
        candidate = Path(result['candidate_dir'])
        journal = Path(result['journal_path'])
        self.assertEqual(result['state'], 'STAGED')
        self.assertFalse(result['reused'])
        self.assertTrue(candidate.is_dir())
        self.assertEqual((candidate / 'b300-stlink').read_bytes(), b'portable-cli')
        mount = (candidate / 'systemd/b300-stlink-ingress.mount.rendered').read_text(encoding='utf-8')
        self.assertIn('uid=2001,gid=2002,mode=0710', mount)
        self.assertNotIn('@AGENT_UID@', mount)
        service = (candidate / 'systemd/b300-stlink-gateway-agent.service').read_text(encoding='utf-8')
        self.assertIn(r'BindsTo=var-spool-b300\x2dstlink-ingress.mount', service)
        journal_record = json.loads(journal.read_text(encoding='utf-8'))
        self.assertEqual(journal_record['status'], 'COMPLETE')
        self.assertEqual(journal_record['bundle_sha256'], self.digest)
        self.assertIn(str(candidate), journal_record['planned_paths'])
        self.assertIn(journal_record['completion_temp'], journal_record['planned_paths'])
        self.assertIn('B300-RUNTIME.sha256', journal_record['members'])
        self.assertFalse((self.root / 'etc/systemd/system').exists())
        self.assertFalse((self.root / 'outside').exists())
        self.assertTrue(all(call in {'inspect', 'id', 'escape', 'fsync_dir'} for call in self.calls))

    def test_repeated_stage_reuses_only_verified_identical_candidate(self):
        first = self._stage()
        before = Path(first['candidate_dir']).stat().st_ino
        second = self._stage()
        self.assertTrue(second['reused'])
        self.assertEqual(second['candidate_dir'], first['candidate_dir'])
        self.assertEqual(Path(second['candidate_dir']).stat().st_ino, before)
        self.assertEqual(len(list(self.opt.glob('.b300-stlink-stage-*.json'))), 1)

    def test_matching_manifest_cannot_hide_changed_candidate_bytes(self):
        first = self._stage()
        candidate = Path(first['candidate_dir'])
        (candidate / 'b300-stlink').write_bytes(b'replaced-cli')
        manifest = candidate / 'B300-RUNTIME.sha256'
        lines = manifest.read_text(encoding='utf-8').splitlines()
        updated = []
        for line in lines:
            if line.endswith(' *b300-stlink'):
                updated.append(hashlib.sha256(b'replaced-cli').hexdigest() + ' *b300-stlink')
            else:
                updated.append(line)
        manifest.write_text('\n'.join(updated) + '\n', encoding='utf-8')
        with self.assertRaises(Exception):
            self._stage()

    def test_existing_candidate_path_reported_as_link_is_not_reused(self):
        first = self._stage()
        candidate = Path(first['candidate_dir'])

        def linked_candidate(path):
            info = self._trusted_stat(path)
            if Path(path) == candidate:
                return SimpleNamespace(**{**vars(info), 'st_mode': stat.S_IFLNK | 0o777})
            return info

        with self.assertRaises(Exception):
            self._stage(path_stat=linked_candidate)

    def test_concurrent_candidate_creation_is_never_overwritten(self):
        def occupied(source, destination):
            destination.mkdir()
            (destination / 'sentinel').write_bytes(b'keep')
            raise FileExistsError('candidate appeared')

        with self.assertRaises(Exception):
            self._stage(promote=occupied)
        candidate = self.install_root / 'candidates' / ('0.23.0-' + self.digest)
        self.assertEqual((candidate / 'sentinel').read_bytes(), b'keep')
        journal = self.opt / ('.b300-stlink-stage-' + self.digest + '.json')
        self.assertEqual(json.loads(journal.read_text())['status'], 'STAGING')

    def test_changed_archive_after_go_plan_refuses_before_journal_or_install_root(self):
        host = self._host()
        plan = self.installer.build_plan(self.bundle, self.digest, host=host,
                                         trust_root=self.root, path_stat=self._trusted_stat)
        self._write_bundle({'changed.txt': b'new bytes'})
        with self.assertRaises(Exception):
            self._stage(host=host, plan=plan)
        self.assertFalse(self.install_root.exists())
        self.assertEqual(list(self.opt.glob('.b300-stlink-stage-*.json')), [])

    def test_archive_mutation_after_journal_is_detected_on_same_open_handle(self):
        changed = [False]
        def mutate_after_journal(path):
            self.calls.append('fsync_dir')
            if Path(path) == self.opt and not changed[0]:
                changed[0] = True
                with self.bundle.open('ab') as stream:
                    stream.write(b'changed-after-journal')

        with self.assertRaises(Exception):
            self._stage(fsync_dir=mutate_after_journal)
        journal = self.opt / ('.b300-stlink-stage-' + self.digest + '.json')
        self.assertTrue(changed[0])
        self.assertEqual(json.loads(journal.read_text())['status'], 'STAGING')

    def test_failed_stage_preserves_write_ahead_journal_and_temp_evidence(self):
        root_syncs = [0]
        def fail_after_journal(path):
            self.calls.append('fsync_dir')
            if Path(path) == self.install_root:
                root_syncs[0] += 1
                if root_syncs[0] == 2:
                    raise OSError('injected fsync failure')
        with self.assertRaises(Exception):
            self._stage(fsync_dir=fail_after_journal)
        journals = list(self.opt.glob('.b300-stlink-stage-*.json'))
        self.assertEqual(len(journals), 1)
        record = json.loads(journals[0].read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'STAGING')
        self.assertTrue(Path(record['temp_dir']).exists())
        self.assertFalse(Path(record['candidate_dir']).exists())
        with self.assertRaises(Exception) as repeated:
            self._stage()
        self.assertEqual(repeated.exception.reason_code, 'RECOVERY_REQUIRED')
        self.assertTrue(Path(record['temp_dir']).exists())

    def test_unsafe_member_is_rejected_without_path_escape(self):
        self._write_bundle({'../outside': b'forbidden'})
        with self.assertRaises(Exception):
            self._stage()
        self.assertFalse((self.root / 'outside').exists())
        self.assertFalse(self.install_root.exists())


if __name__ == '__main__':
    unittest.main()
