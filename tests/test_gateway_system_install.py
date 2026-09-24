from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.gateway_system_mode import ingress_mount_isolated


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


if __name__ == '__main__':
    unittest.main()
