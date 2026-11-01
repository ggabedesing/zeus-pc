import unittest
from unittest.mock import patch

from zeus_pc import startup


class StartupToggleTests(unittest.TestCase):
    def setUp(self):
        self.store = {startup.RUN: {'Test app': ('"C:\\Apps\\app.exe" --start', 2)}, startup.BACKUP: {}}
        def put(path, name, value):
            self.store[path][name] = value
        def delete(path, name):
            del self.store[path][name]
        for name, value in {'_values': lambda path: dict(self.store[path]), '_put': put, '_delete': delete}.items():
            p = patch.object(startup, name, value)
            p.start()
            self.addCleanup(p.stop)
        approval = patch.object(startup, '_approval_allows', lambda name: True, create=True)
        approval.start()
        self.addCleanup(approval.stop)

    def test_disable_and_enable_preserve_exact_command_and_registry_type(self):
        startup.set_startup_enabled('Test app', False)
        self.assertEqual(self.store[startup.RUN], {})
        self.assertEqual(self.store[startup.BACKUP]['Test app'], ('"C:\\Apps\\app.exe" --start', 2))
        startup.set_startup_enabled('Test app', True)
        self.assertEqual(self.store[startup.RUN]['Test app'], ('"C:\\Apps\\app.exe" --start', 2))
        self.assertEqual(self.store[startup.BACKUP], {})

    def test_existing_destination_is_never_overwritten(self):
        self.store[startup.BACKUP]['Test app'] = ('older-command', 1)
        with self.assertRaises(ValueError):
            startup.set_startup_enabled('Test app', False)
        self.assertEqual(self.store[startup.BACKUP]['Test app'], ('older-command', 1))
        self.assertIn('Test app', self.store[startup.RUN])

    def test_windows_disabled_entry_is_not_offered_as_a_new_optimization(self):
        with patch.object(startup, '_approval_allows', lambda name: False, create=True):
            self.assertEqual(startup.list_startup(), [])

    def test_restore_does_not_claim_to_enable_a_windows_blocked_entry(self):
        startup.set_startup_enabled('Test app', False)
        with patch.object(startup, '_approval_allows', lambda name: False, create=True):
            with self.assertRaises(ValueError):
                startup.set_startup_enabled('Test app', True)
        self.assertNotIn('Test app', self.store[startup.RUN])


if __name__ == '__main__':
    unittest.main()
