import json
import tempfile
import unittest
from pathlib import Path
from b300_core.project_profiles import ProjectProfile, ProjectProfileStore

class ProjectProfileTests(unittest.TestCase):
    def test_schema_one_migrates_on_write_preserving_default_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'projects.json'
            old = {'schema_version': 1, 'default_id': 'old', 'projects': [{'id':'old','name':'Old','workspace':str(Path(folder)), 'symbols':str(Path(folder)/'old.axf')}]}
            path.write_text(json.dumps(old))
            store = ProjectProfileStore(path)
            profile = store.default()
            self.assertIsNone(profile.application_hex)
            self.assertEqual(profile.target_family, '')
            self.assertEqual(json.loads(path.read_text()), old)
            store.set_default('old')
            self.assertEqual(json.loads(path.read_text())['schema_version'], 2)
            self.assertEqual(store.default(), profile)

    def test_hex_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'app.axf').touch(); (root/'app.hex').touch()
            profile = ProjectProfile.create('Main', root, root/'app.axf', application_hex=root/'app.hex', target_family='STM32F407')
            store = ProjectProfileStore(root/'profiles.json')
            store.upsert(profile)
            self.assertEqual(store.default(), profile)
            self.assertEqual(set(profile.record()), {'id','name','workspace','symbols','application_hex','target_family'})
            with self.assertRaises(ValueError):
                ProjectProfile.create('Main', root, root/'app.axf', application_hex=root/'missing.hex')
            with self.assertRaises(ValueError):
                ProjectProfile.create('Main', root, root/'app.axf', application_hex=root/'app.bin', require_exists=False)
            with self.assertRaises(ValueError):
                ProjectProfile.create('Main', root, root/'app.axf', target_family='bad\nvalue')

    def test_unknown_secret_fields_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'profiles.json'
            path.write_text(json.dumps({'schema_version':2,'default_id':None,'projects':[{'id':'p','name':'P','workspace':folder,'symbols':'app.axf','password':'secret'}]}))
            with self.assertRaises(RuntimeError): ProjectProfileStore(path).list()

    def test_schema_two_rejects_structured_optional_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profiles.json'
            path.write_text(json.dumps({'schema_version':2,'default_id':None,'projects':[{'id':'p','name':'P','workspace':folder,'symbols':'app.axf','target_family':{'password':'secret'}}]}))
            with self.assertRaises(RuntimeError): ProjectProfileStore(path).list()
