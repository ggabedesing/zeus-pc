import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zeus_pc import layout_runtime as layout


class FakeNative:
    supported = True

    def __init__(self):
        self.bars = {
            10: dict(hwnd=10, pid=50, class_name='Shell_TrayWnd', visible=True),
            20: dict(hwnd=20, pid=50, class_name='Shell_SecondaryTrayWnd', visible=False),
        }
        self.processes = {os.getpid(): 'parent', 800: 'worker'}

    def list_bars(self):
        return copy.deepcopy(list(self.bars.values()))

    def identity(self, hwnd):
        return copy.deepcopy(self.bars.get(hwnd))

    def set_visible(self, hwnd, visible):
        self.bars[hwnd]['visible'] = visible

    def process_stamp(self, pid):
        return self.processes.get(pid)

    def process_alive(self, pid, stamp=None):
        return pid in self.processes and (stamp is None or self.processes[pid] == stamp)

    def terminate_worker(self, pid, stamp):
        if self.process_alive(pid, stamp):
            self.processes.pop(pid)


class FakeProcess:
    pid = 800

    def __init__(self, native):
        self.native = native

    def poll(self):
        return None if self.native.process_alive(self.pid) else 0


class LayoutRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'layout-runtime'
        self.native = FakeNative()
        self.spawned = []
        def spawn(command, **kwargs):
            self.spawned.append((command, kwargs))
            self.native.processes[800] = 'worker'
            return FakeProcess(self.native)
        for name, value in [('_native', self.native), ('_spawn', spawn)]:
            patcher = patch.object(layout, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runtime = layout.Runtime(self.directory)

    def state(self):
        return json.loads((self.directory / 'state.json').read_text(encoding='utf-8'))

    def write_state(self, state):
        (self.directory / 'state.json').write_text(json.dumps(state), encoding='utf-8')

    def test_start_is_explicit_single_worker_and_defaults_to_leaving_taskbars_visible(self):
        self.assertFalse(self.runtime.status()['running'])
        self.assertEqual(self.spawned, [])
        first = self.runtime.start('dock')
        self.assertTrue(first['running'])
        self.assertEqual(first['layout'], 'dock')
        self.assertFalse(first['hide_native_taskbar'])
        self.assertTrue(self.native.bars[10]['visible'])
        self.runtime.start('dock')
        self.assertEqual(len(self.spawned), 1)
        command, options = self.spawned[0]
        self.assertIn('--layout-worker', command)
        self.assertIn('--parent-pid', command)
        self.assertNotIn('--hide-native-taskbar', command)
        self.assertIs(options['shell'], False)
        self.assertNotIn('token', first)

    def test_recovery_shows_only_owned_originally_visible_bars_and_ignores_reused_handles(self):
        self.runtime.start('dock', True)
        state = self.state()
        layout._hide_bars(self.directory, state)
        self.assertFalse(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])
        self.native.processes.pop(800)
        recovered = self.runtime.status()
        self.assertFalse(recovered['running'])
        self.assertEqual(recovered['state'], 'ERROR')
        self.assertTrue(recovered['error'])
        self.assertTrue(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])
        self.runtime.start('sidebar', True)
        layout._hide_bars(self.directory, self.state())
        self.native.bars[10]['pid'] = 99
        self.native.processes.pop(800)
        self.runtime.status()
        self.assertFalse(self.native.bars[10]['visible'])

    def test_stop_requests_worker_exit_then_recovers_if_worker_had_to_be_terminated(self):
        self.runtime.start('sidebar', True)
        layout._hide_bars(self.directory, self.state())
        with patch.object(layout, '_STOP_TIMEOUT', 0):
            stopped = self.runtime.stop()
        self.assertFalse(stopped['running'])
        self.assertTrue(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])

    def test_validation_never_spawns_a_worker_for_untrusted_values(self):
        for name, hide in [('custom', False), ('dock', 'yes'), ('../dock', False), ('sidebar', 1)]:
            with self.assertRaises(ValueError):
                self.runtime.start(name, hide)
        self.assertEqual(self.spawned, [])

    def test_second_runtime_instance_observes_same_worker_and_switch_restores_taskbar(self):
        self.runtime.start('dock', True)
        layout._hide_bars(self.directory, self.state())
        second = layout.Runtime(self.directory)
        second.start('dock', True)
        self.assertEqual(len(self.spawned), 1)
        with patch.object(layout, '_STOP_TIMEOUT', 0):
            switched = second.start('sidebar')
        self.assertEqual(switched['layout'], 'sidebar')
        self.assertFalse(switched['hide_native_taskbar'])
        self.assertTrue(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])

    def test_ui_failure_leaves_native_taskbar_unchanged_and_reports_error(self):
        self.runtime.start('dock', True)
        state = self.state()
        with patch.object(layout, '_create_window', side_effect=RuntimeError('Tk unavailable')), \
                patch.object(layout.os, 'getpid', return_value=800):
            result = layout.worker_main(['--layout-worker', 'dock', '--runtime-dir', str(self.directory),
                                        '--token', state['token'], '--parent-pid', str(state['parent_pid']),
                                        '--hide-native-taskbar'])
        self.assertEqual(result, 1)
        self.assertIn('Tk unavailable', self.runtime.status()['error'])
        self.assertTrue(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])

    def test_worker_rejects_wrong_token_before_creating_a_window_or_hiding_taskbar(self):
        self.runtime.start('dock', True)
        with patch.object(layout, '_create_window', side_effect=AssertionError('no UI')):
            with self.assertRaises(ValueError):
                layout.worker_main(['--layout-worker', 'dock', '--runtime-dir', str(self.directory),
                                    '--token', '0' * 64, '--parent-pid', str(os.getpid()),
                                    '--hide-native-taskbar'])
        self.assertTrue(self.native.bars[10]['visible'])

    def test_parent_exit_closes_worker_and_restores_taskbar_without_runtime_polling(self):
        self.runtime.start('sidebar', True)
        state = self.state()
        fake_window = FakeWindow()
        self.native.processes.pop(os.getpid())
        with patch.object(layout, '_create_window', return_value=fake_window), \
                patch.object(layout.os, 'getpid', return_value=800):
            result = layout.worker_main(['--layout-worker', 'sidebar', '--runtime-dir', str(self.directory),
                                        '--token', state['token'], '--parent-pid', str(state['parent_pid']),
                                        '--hide-native-taskbar'])
        self.assertEqual(result, 0)
        self.assertTrue(fake_window.destroyed)
        self.assertTrue(self.native.bars[10]['visible'])
        self.assertFalse(self.native.bars[20]['visible'])
        self.assertEqual(self.state()['state'], 'STOPPED')


class FakeWindow:
    def __init__(self):
        self.callback = None
        self.destroyed = False

    def after(self, delay, callback):
        self.callback = callback

    def mainloop(self):
        if self.callback:
            self.callback()

    def destroy(self):
        if self.destroyed:
            raise RuntimeError('Destroyed twice')
        self.destroyed = True


if __name__ == '__main__':
    unittest.main()
