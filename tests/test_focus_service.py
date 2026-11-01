import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.focus import FocusService
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import TransactionEngine, build_default_registry


class FocusServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ZeusDatabase(Path(self.temp.name) / 'focus.db')
        self.db.initialize()
        self.animation = True
        self.startup = {'name': 'Test chat', 'enabled': True, 'command': 'test-chat.exe'}
        def write_animation(value):
            self.animation = value
        def write_startup(name, enabled):
            self.startup['enabled'] = enabled
        replacements = {
            'read_animations': lambda: self.animation,
            'write_animations': write_animation,
            'list_startup': lambda: [dict(self.startup)],
            'set_startup_enabled': write_startup,
        }
        for name, value in replacements.items():
            p = patch('zeus_pc.transaction.' + name, value)
            p.start()
            self.addCleanup(p.stop)
        self.engine = TransactionEngine(self.db, build_default_registry())
        self.service = FocusService(self.db, self.engine, lambda: {
            'supported': True, 'animations_enabled': self.animation,
            'startup': [dict(self.startup)], 'power_plans': [],
            '_temp_manifest': [{'path': 'private-temp-path'}],
        })

    def test_selected_actions_apply_once_and_survive_service_restart(self):
        report = self.service.analyze('daily')
        result = self.service.apply(report['id'], ['animations'])
        self.assertEqual(result['status'], 'verified')
        self.assertFalse(self.animation)
        self.assertTrue(self.startup['enabled'])
        self.assertNotIn('_temp_manifest', result['before'])
        restarted = FocusService(self.db, self.engine)
        repeated = restarted.apply(report['id'], ['animations'])
        self.assertEqual(repeated['results'][0]['transaction_id'], result['results'][0]['transaction_id'])
        self.assertEqual(len(self.db.recent_transactions()), 1)

    def test_undo_restores_startup_and_animations_without_replaying(self):
        report = self.service.analyze('daily')
        result = self.service.apply(report['id'], ['animations', 'startup-0'])
        self.assertFalse(self.startup['enabled'])
        undone = self.service.undo(result['id'])
        self.assertTrue(undone['undone'])
        self.assertTrue(self.animation)
        self.assertTrue(self.startup['enabled'])
        self.service.undo(result['id'])
        self.assertEqual(len(self.db.recent_transactions()), 4)

    def test_expired_report_or_unoffered_action_cannot_change_state(self):
        report = self.service.analyze('daily')
        for ids in ([], ['shell'], ['animations', 'animations']):
            with self.assertRaises(ValueError):
                self.service.apply(report['id'], ids)
        report['created_at'] = time.time() - 301
        self.db.set_setting('focus_report', report)
        with self.assertRaises(ValueError):
            self.service.apply(report['id'], ['animations'])
        self.assertTrue(self.animation)
        self.assertEqual(self.db.recent_transactions(), [])

    def test_failed_action_stops_later_actions_and_reports_partial_result(self):
        report = self.service.analyze('daily')
        def fail(_):
            raise OSError('Windows rejected the change')
        with patch('zeus_pc.transaction.write_animations', fail):
            result = self.service.apply(report['id'], ['animations', 'startup-0'])
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['results'][0]['ok'])
        self.assertTrue(self.startup['enabled'])
        self.assertEqual(len(result['results']), 1)

    def test_public_report_never_exposes_internal_file_manifest(self):
        report = self.service.analyze('daily')
        self.assertNotIn('_temp_manifest', self.service.public_report(report)['snapshot'])

    def test_restart_recovers_verified_action_after_response_was_interrupted(self):
        report = self.service.analyze('daily')
        execute = self.engine.execute
        def execute_then_crash(tid):
            execute(tid)
            raise KeyboardInterrupt('simulated process death')
        with patch.object(self.engine, 'execute', execute_then_crash):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(report['id'], ['animations', 'startup-0'])
        restarted = FocusService(self.db, self.engine, self.service.collector)
        recovered = restarted.apply(report['id'], ['animations'])
        self.assertTrue(recovered['results'][0]['ok'])
        self.assertEqual(recovered['status'], 'partial')
        self.assertTrue(restarted.undo(report['id'])['undone'])
        self.assertTrue(self.animation)

    def test_restart_verifies_an_applied_setting_before_the_journal_finished(self):
        report = self.service.analyze('daily')
        transition = self.db.transition_transaction
        def crash_before_applied(*args, **kwargs):
            if kwargs.get('to_state') == 'APPLIED':
                raise KeyboardInterrupt('process died after Windows accepted the write')
            return transition(*args, **kwargs)
        with patch.object(self.db, 'transition_transaction', crash_before_applied):
            with self.assertRaises(KeyboardInterrupt):
                self.service.apply(report['id'], ['animations'])
        self.assertFalse(self.animation)
        restarted = FocusService(self.db, self.engine, self.service.collector)
        recovered = restarted.history()[0]
        self.assertTrue(recovered['results'][0]['ok'])
        self.assertTrue(restarted.undo(report['id'])['undone'])
        self.assertTrue(self.animation)



if __name__ == '__main__':
    unittest.main()
