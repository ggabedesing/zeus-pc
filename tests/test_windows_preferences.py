import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc import windows_preferences as prefs
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import TransactionEngine, ActionRegistry


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = {}
        def write(path, name, state):
            if state['exists']:
                self.store[path, name] = dict(state)
            else:
                self.store.pop((path, name), None)
        for name, value in {'read_value': lambda p, n: dict(self.store.get((p, n), {'exists': False})),
                            'write_value': write, 'notify': lambda: None,
                            'data_dir': lambda: Path(self.temp.name)}.items():
            p = patch.object(prefs, name, value, create=True)
            p.start(); self.addCleanup(p.stop)
        db = ZeusDatabase(Path(self.temp.name) / 'test.db')
        db.initialize()
        registry = ActionRegistry()
        prefs.register_actions(registry)
        self.engine = TransactionEngine(db, registry)

    def execute(self, action, args):
        tx = self.engine.prepare(action, args)
        self.engine.consume_consent(tx.transaction_id, tx.consent_token)
        return self.engine.execute(tx.transaction_id)

    def test_apply_and_undo_restore_absence_instead_of_guessing_default(self):
        result = self.execute('SET_WINDOWS_PREFERENCE', {'id': 'file_extensions', 'value': True})
        self.assertTrue(result['ok'])
        self.assertTrue(prefs.preference_state('file_extensions')['value'])
        inverse = result['transaction']['rollback']
        self.assertTrue(self.execute(inverse['action_id'], inverse['args'])['ok'])
        self.assertEqual(self.store, {})

    def test_invalid_values_cannot_write_other_registry_settings(self):
        for args in ({'id': 'security', 'value': False}, {'id': 'file_extensions', 'value': 1},
                     {'id': 'file_extensions', 'value': True, 'path': 'elsewhere'}):
            with self.assertRaises(RuntimeError):
                self.engine.prepare('SET_WINDOWS_PREFERENCE', args)
        self.assertEqual(self.store, {})

    def test_gpu_preference_preserves_unrelated_fields(self):
        original = 'VRROptimizeEnable=1;GpuPreference=1;CustomFlag=abc;'
        self.assertEqual(prefs.merge_gpu_preference(original, 'high'),
                         'VRROptimizeEnable=1;GpuPreference=2;CustomFlag=abc;')
        self.assertEqual(prefs.merge_gpu_preference(original, 'system'),
                         'VRROptimizeEnable=1;CustomFlag=abc;')

    def test_gpu_preference_can_be_verified_and_undone_after_app_closes(self):
        import hashlib
        path = Path(self.temp.name) / 'example.exe'
        path.write_bytes(b'fixture, never executed')
        app_id = hashlib.sha256(str(path).lower().encode()).hexdigest()[:32]
        app = {'id': app_id, 'name': 'Example', 'path': str(path), 'preference': 'system'}
        with patch.object(prefs, 'applications', return_value=[app]):
            result = self.execute('SET_GPU_PREFERENCE', {'application_id': app_id, 'preference': 'high'})
        with patch.object(prefs, 'applications', return_value=[]):
            self.assertTrue(self.engine.verify_target('SET_GPU_PREFERENCE', {'application_id': app_id, 'preference': 'high'})['ok'])
            inverse = result['transaction']['rollback']
            self.assertTrue(self.execute(inverse['action_id'], inverse['args'])['ok'])
        self.assertEqual(self.store, {})

    def test_user_can_register_a_closed_game_without_executing_it(self):
        path = Path(self.temp.name) / 'closed-game.exe'
        path.write_bytes(b'fixture, not executable')
        app = prefs.register_application(str(path))
        self.assertEqual(prefs.registered_applications()[0]['id'], app['id'])
        self.assertEqual(self.store, {})
        with self.assertRaises(ValueError):
            prefs.register_application('relative.exe')


if __name__ == '__main__':
    unittest.main()
