import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.focus import build_report
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import TransactionEngine, build_default_registry

BALANCED = '381b4222-f694-41f0-9685-ff5bb260df2e'
FAST = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'


def machine(**changes):
    result = {
        'supported': True, 'ac_connected': True, 'animations_enabled': True,
        'cpu_pct': 12, 'ram_used_pct': 45, 'disk_free_pct': 35,
        'power_plans': [
            {'guid': BALANCED, 'name': 'Equilibrado', 'active': True},
            {'guid': FAST, 'name': 'Alto desempenho', 'active': False},
        ],
    }
    result.update(changes)
    return result


class FocusPlanTests(unittest.TestCase):
    def test_gaming_on_ac_offers_existing_performance_plan(self):
        report = build_report(machine(), 'gaming')
        energy = next(a for a in report['actions'] if a['id'] == 'energy')
        self.assertEqual(energy['args'], {'guid': FAST})

    def test_battery_or_unknown_power_never_offers_performance_plan(self):
        for ac in (False, None):
            report = build_report(machine(ac_connected=ac), 'gaming')
            self.assertFalse(any(a['id'] == 'energy' for a in report['actions']))

    def test_unknown_custom_plan_is_not_guessed_from_its_name(self):
        report = build_report(machine(power_plans=[
            {'guid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'name': 'Performance', 'active': True},
        ]), 'gaming')
        self.assertFalse(any(a['id'] == 'energy' for a in report['actions']))

    def test_already_configured_machine_has_no_redundant_changes(self):
        report = build_report(machine(animations_enabled=False), 'daily')
        self.assertEqual(report['actions'], [])
        self.assertEqual(report['status'], 'ready')

    def test_unsupported_machine_has_no_executable_actions(self):
        report = build_report(machine(supported=False), 'gaming')
        self.assertEqual(report['actions'], [])
        self.assertEqual(report['status'], 'unsupported')

    def test_missing_readings_stay_unknown_and_pressure_creates_actionable_notice(self):
        report = build_report(machine(cpu_pct=None, ram_used_pct=94, disk_free_pct=5), 'daily')
        self.assertIsNone(report['snapshot']['cpu_pct'])
        self.assertEqual({n['id'] for n in report['notices']}, {'memory', 'storage'})
        self.assertFalse(any(a['action_id'] in {'KILL_PROCESS', 'DELETE_FILES'} for a in report['actions']))

    def test_invalid_goal_is_rejected(self):
        with self.assertRaises(ValueError):
            build_report(machine(), 'shell')


class AnimationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ZeusDatabase(Path(self.temp.name) / 'test.db')
        self.db.initialize()
        self.enabled = True
        def write(value):
            self.enabled = value
        for name, replacement in (
            ('read_animations', lambda: self.enabled), ('write_animations', write),
        ):
            p = patch('zeus_pc.transaction.' + name, replacement, create=True)
            p.start()
            self.addCleanup(p.stop)
        self.engine = TransactionEngine(self.db, build_default_registry())

    def test_animation_change_requires_consent_verifies_and_can_be_restored(self):
        prepared = self.engine.prepare('SET_UI_ANIMATIONS', {'enabled': False})
        self.assertEqual(prepared.before_state, {'enabled': True})
        with self.assertRaises(RuntimeError):
            self.engine.execute(prepared.transaction_id)
        self.engine.consume_consent(prepared.transaction_id, prepared.consent_token)
        result = self.engine.execute(prepared.transaction_id)
        self.assertTrue(result['ok'])
        self.assertFalse(self.enabled)
        reverse = self.engine.prepare('SET_UI_ANIMATIONS', {'enabled': True})
        self.engine.consume_consent(reverse.transaction_id, reverse.consent_token)
        self.assertTrue(self.engine.execute(reverse.transaction_id)['ok'])
        self.assertTrue(self.enabled)

    def test_non_boolean_animation_argument_is_rejected(self):
        for args in ({'enabled': 'false'}, {'enabled': 0}, {'enabled': True, 'script': 'x'}):
            with self.assertRaises(RuntimeError):
                self.engine.prepare('SET_UI_ANIMATIONS', args)


if __name__ == '__main__':
    unittest.main()
