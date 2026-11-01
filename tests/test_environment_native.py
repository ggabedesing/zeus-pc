"""Real Tk widgets kept withdrawn; no desktop effects, hotkeys or app launches."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from zeus_pc.environment import EnvironmentStore
from zeus_pc.environment_ui import EnvironmentUI


@unittest.skipUnless(os.name == 'nt', 'Tk/Windows widget verification')
class HiddenNativeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patches = [patch.dict(os.environ, {'ZEUS_DATA_DIR': self.temp.name}),
                   patch('zeus_pc.environment_ui.Hotkey'),
                   patch('zeus_pc.environment_ui.DesktopLayer'),
                   patch('zeus_pc.layout_runtime._native.work_area', return_value=(0, 0, 1280, 760)),
                   patch('zeus_pc.layout_runtime._native.round_own_window'),
                   patch.object(tk.Toplevel, 'deiconify'), patch.object(tk.Toplevel, 'lift')]
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        EnvironmentStore().update({'widgets': {'notes': True}})
        self.app = EnvironmentUI(Mock(), {})
        self.app.hotkey.poll.return_value = False
        self.app.root.update()
        self.addCleanup(self.close)

    def close(self):
        self.app.shutdown(); self.app.root.destroy()

    def test_every_widget_renders_and_notes_survive_live_theme_change(self):
        self.assertEqual(set(self.app.panels), {'clock', 'system', 'notes', 'topbar', 'dock'})
        self.assertTrue(all(panel.canvas.find_all() for panel in self.app.panels.values()))
        notes = self.app.panels['notes']
        notes.editor.delete('1.0', 'end'); notes.editor.insert('1.0', 'Minha ideia')
        notes.flush_notes()
        self.app.store.update({'theme': 'light', 'density': 'compact'})
        self.app.last_watch = 0
        self.app.tick(); self.app.root.update()
        self.assertEqual(self.app.config['theme'], 'light')
        self.assertEqual(self.app.panels['notes'].editor.get('1.0', 'end-1c'), 'Minha ideia')

    def test_launcher_search_and_keyboard_selection_do_not_launch_during_preview(self):
        self.app.launcher()
        self.app.query.set('configuracoes')
        self.app.root.update()
        self.assertEqual(self.app.matches[0]['id'], 'builtin:settings')
        with patch('zeus_pc.layout_runtime._fixed_action') as launch:
            self.app.open_selected()
            launch.assert_called_once_with('settings')
        self.assertIsNone(self.app.popup)

    def test_notes_pending_save_is_cancelled_when_panel_rebuilds(self):
        panel = self.app.panels['notes']
        panel.save_notes(None)
        timer = panel.note_timer
        panel.close()
        self.assertNotIn(timer, self.app.root.tk.call('after', 'info'))
        del self.app.panels['notes']

    def test_shutdown_cancels_all_owned_timers_before_destroying_widgets(self):
        self.app.launcher()
        self.assertTrue(self.app.root.tk.call('after', 'info'))
        self.app.shutdown()
        self.assertFalse(self.app.root.tk.call('after', 'info'))
