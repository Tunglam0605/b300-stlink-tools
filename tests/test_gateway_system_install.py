from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

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


if __name__ == '__main__':
    unittest.main()
