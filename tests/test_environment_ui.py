import unittest
import threading
from unittest.mock import Mock
from zeus_pc.environment_ui import Hotkey, EnvironmentUI


class EnvironmentUiTests(unittest.TestCase):
    def test_hotkey_conflict_is_nonfatal_and_never_unregisters_someone_else(self):
        user = Mock()
        user.RegisterHotKey.return_value = 0
        key = Hotkey(user)
        self.assertFalse(key.registered)
        self.assertFalse(key.poll())
        key.close()
        user.UnregisterHotKey.assert_not_called()

    def test_hotkey_is_unregistered_exactly_once(self):
        user = Mock()
        user.RegisterHotKey.return_value = 1
        key = Hotkey(user)
        self.assertTrue(key.registered)
        key.close(); key.close()
        user.UnregisterHotKey.assert_called_once_with(None, 0x5A31)

    def test_hotkey_queue_has_its_own_thread_so_tk_cannot_consume_messages(self):
        calls = []
        user = Mock()
        user.RegisterHotKey.side_effect = lambda *_: calls.append(('register', threading.get_ident())) or 1
        user.PeekMessageW.return_value = 0
        user.UnregisterHotKey.side_effect = lambda *_: calls.append(('unregister', threading.get_ident())) or 1
        key = Hotkey(user)
        key.close()
        self.assertNotEqual(calls[0][1], threading.get_ident())
        self.assertEqual(calls[0][1], calls[1][1])

    def test_dock_filters_removed_apps_and_deduplicates_fixed_shortcuts(self):
        app = EnvironmentUI.__new__(EnvironmentUI)
        app.apps = [{'id': 'builtin:files', 'name': 'Arquivos'}, {'id': 'a' * 64, 'name': 'Jogo'}]
        app.config = {'pinned': ['builtin:files', 'a' * 64, 'b' * 64]}
        self.assertEqual([item['name'] for item in app.dock_apps()], ['Arquivos', 'Jogo'])

    def test_shutdown_saves_notes_before_unregistering_hotkey_and_is_idempotent(self):
        app = EnvironmentUI.__new__(EnvironmentUI)
        panel = Mock(); panel.name = 'notes'
        app.panels, app.hotkey, app.closing = {'notes': panel}, Mock(), False
        app.root = Mock()
        app.root.tk.call.return_value = ()
        app.shutdown(); app.shutdown()
        panel.flush_notes.assert_called_once()
        app.hotkey.close.assert_called_once()
