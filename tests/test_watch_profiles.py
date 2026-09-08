from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.watch_profiles import (
    WatchPolicy, format_watch_value, load_watch_policies, save_watch_policies,
    watch_policy_groups, watch_policies_for_group,
)


class WatchProfilesTests(unittest.TestCase):
    def test_formats_and_transforms_engineering_value(self):
        policy = WatchPolicy('motor.rpm', display_format='hex', unit='rpm', scale=0.5, offset=1)
        self.assertEqual(format_watch_value(policy, 10), '0x6 rpm')
        self.assertEqual(format_watch_value(WatchPolicy('state', display_format='binary'), 5), '0b101')
        self.assertEqual(format_watch_value(WatchPolicy('v', display_format='float'), 3), '3.0')

    def test_sidecar_roundtrip_is_atomic_and_rejects_unknown_or_malformed_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / '.b300-watch-policies.json'
            saved = save_watch_policies(path, (WatchPolicy('motor.rpm', minimum=0, maximum=5000),))
            self.assertEqual(saved, path)
            self.assertEqual(load_watch_policies(path), (WatchPolicy('motor.rpm', minimum=0.0, maximum=5000.0),))
            path.write_text(json.dumps({'schema_version': 1, 'policies': [{'path': 'x', 'secret': 'no'}]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unknown'):
                load_watch_policies(path)

    def test_groups_roundtrip_and_filter_without_breaking_legacy_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / '.b300-watch-policies.json'
            save_watch_policies(path, (
                WatchPolicy('motor.rpm', group='Drive'),
                WatchPolicy('battery.voltage', group='Power'),
                WatchPolicy('state'),
            ))
            policies = load_watch_policies(path)
            self.assertEqual(watch_policy_groups(policies), ('Drive', 'General', 'Power'))
            self.assertEqual(
                tuple(item.path for item in watch_policies_for_group(policies, 'Drive')),
                ('motor.rpm',),
            )
            path.write_text(json.dumps({
                'schema_version': 1,
                'policies': [{'path': 'legacy'}],
            }), encoding='utf-8')
            self.assertEqual(load_watch_policies(path)[0].group, 'General')


if __name__ == '__main__':
    unittest.main()
