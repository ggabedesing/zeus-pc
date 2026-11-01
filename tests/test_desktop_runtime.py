"""Runtime contracts without creating windows or starting renderer processes."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zeus_pc import desktop_runtime as desktop


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = desktop.Runtime(self.temp.name)
        self.folder = Path(self.temp.name) / 'desktop-runtime'

    def record(self, **changes):
        self.folder.mkdir(parents=True, exist_ok=True)
        data = dict(state='running', scene='aurora', pid=123, process_started='born-123',
                    token='a' * 32, updated_at=time.time(), message='')
        data.update(changes)
        (self.folder / 'status.json').write_text(json.dumps(data), encoding='utf-8')
        return data

    def test_default_is_stopped_and_catalog_does_not_start_anything(self):
        self.assertEqual(self.runtime.status()['state'], 'stopped')
        self.assertFalse(self.runtime.status()['running'])
        self.assertEqual(self.runtime.status()['fps_limit'], 24)
        self.assertFalse(self.folder.exists())
        scenes = desktop.catalog()['scenes']
        self.assertEqual({s['id'] for s in scenes}, {'aurora', 'particles', 'waves'})
        self.assertTrue(all(s['colors'] and s['description'] for s in scenes))

    def test_invalid_scene_never_creates_control_files(self):
        with self.assertRaises(ValueError):
            self.runtime.start('../anything')
        self.assertFalse(self.folder.exists())

    def test_start_uses_closed_child_command_and_does_not_claim_running(self):
        with patch.object(desktop, '_windows_supported', return_value=True), \
             patch.object(desktop, '_process_identity', return_value='born-123'), \
             patch.object(desktop.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)) as launch:
            result = self.runtime.start('aurora')
        self.assertEqual(result['state'], 'starting')
        command = launch.call_args.args[0]
        self.assertIn('--desktop-worker', command)
        self.assertEqual(command[command.index('--desktop-worker') + 1], 'aurora')
        self.assertEqual(command[command.index('--runtime-dir') + 1], str(self.folder.resolve()))
        self.assertFalse(launch.call_args.kwargs.get('shell', False))
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['pid'], 123)

    def test_start_same_live_scene_is_idempotent(self):
        self.record()
        with patch.object(desktop, '_windows_supported', return_value=True), \
             patch.object(desktop, '_process_identity', return_value='born-123'), \
             patch.object(desktop.subprocess, 'Popen') as launch:
            self.assertEqual(self.runtime.start('aurora')['state'], 'running')
            with self.assertRaises(ValueError):
                self.runtime.start('waves')
        launch.assert_not_called()

    def test_pid_reuse_is_stale_and_stop_never_signals_foreign_process(self):
        self.record()
        with patch.object(desktop, '_process_identity', return_value='different-process'):
            self.assertEqual(self.runtime.status()['state'], 'stopped')
            self.assertEqual(self.runtime.stop()['state'], 'stopped')
        self.assertFalse((self.folder / 'stop.json').exists())

    def test_stop_requests_only_matching_worker_and_is_cooperative(self):
        self.record()
        with patch.object(desktop, '_process_identity', return_value='born-123'):
            self.assertEqual(self.runtime.stop()['state'], 'stopping')
        self.assertEqual(json.loads((self.folder / 'stop.json').read_text())['token'], 'a' * 32)

    def test_status_exposes_confirmed_running_and_paused_as_active(self):
        with patch.object(desktop, '_process_identity', return_value='born-123'):
            for state in ('running', 'paused'):
                self.record(state=state)
                result = self.runtime.status()
                self.assertTrue(result['running'])
                self.assertEqual(result['fps'], 24)

    def test_restart_during_startup_does_not_spawn_a_second_worker(self):
        self.record(state='starting', pid=None, process_started=None)
        with patch.object(desktop, '_windows_supported', return_value=True), \
             patch.object(desktop.subprocess, 'Popen') as launch:
            result = self.runtime.start('aurora')
        self.assertEqual(result['state'], 'starting')
        launch.assert_not_called()

    def test_stop_during_startup_uses_session_token_without_needing_pid(self):
        self.record(state='starting', pid=None, process_started=None)
        result = self.runtime.stop()
        self.assertEqual(result['state'], 'stopping')
        self.assertEqual(json.loads((self.folder / 'stop.json').read_text())['token'], 'a' * 32)

    def test_failed_spawn_returns_explicit_failure(self):
        with patch.object(desktop, '_windows_supported', return_value=True), \
             patch.object(desktop.subprocess, 'Popen', side_effect=OSError('cannot spawn')):
            result = self.runtime.start('waves')
        self.assertEqual(result['state'], 'failed')
        self.assertIn('cannot spawn', result['message'])

    def test_source_and_frozen_child_commands_do_not_start_normal_launcher(self):
        with patch.object(desktop.sys, 'frozen', False, create=True):
            source = desktop._worker_command('waves', self.folder, 'a' * 32)
        self.assertEqual(source[1:3], ['-m', 'zeus_pc.desktop_runtime'])
        with patch.object(desktop.sys, 'frozen', True, create=True):
            frozen = desktop._worker_command('waves', self.folder, 'a' * 32)
        self.assertEqual(frozen[1:3], ['--desktop-worker', 'waves'])

    def test_worker_rejects_stale_owner_without_creating_renderer(self):
        self.record(token='b' * 32)
        with patch.object(desktop, '_NativeRenderer') as renderer:
            code = desktop._worker_run('aurora', self.folder, 'a' * 32)
        self.assertNotEqual(code, 0)
        renderer.assert_not_called()
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['token'], 'b' * 32)

    def test_unavailable_desktop_host_reports_failed_without_overlay_fallback(self):
        self.record(state='starting')
        with patch.object(desktop, '_process_identity', return_value='born-123'), \
             patch.object(desktop, '_NativeRenderer', side_effect=OSError('WorkerW unavailable')):
            code = desktop._worker_run('aurora', self.folder, 'a' * 32)
        self.assertEqual(code, 1)
        state = json.loads((self.folder / 'status.json').read_text())
        self.assertEqual(state['state'], 'failed')
        self.assertIn('WorkerW', state['message'])

    def test_worker_closes_renderer_and_stop_is_token_scoped(self):
        self.record(state='starting')
        (self.folder / 'stop.json').write_text(json.dumps({'token': 'b' * 32}))
        observed = []
        class Renderer:
            def __init__(self, scene):
                observed.append(scene)
            def run(self, should_stop, publish):
                self.test_stop = should_stop()
                observed.append(self.test_stop)
                publish('running', '')
                (self_folder / 'stop.json').write_text(json.dumps({'token': 'a' * 32}))
                observed.append(should_stop())
            def close(self):
                observed.append('closed')
        self_folder = self.folder
        with patch.object(desktop, '_process_identity', return_value='born-123'), \
             patch.object(desktop, '_NativeRenderer', Renderer):
            self.assertEqual(desktop._worker_run('aurora', self.folder, 'a' * 32), 0)
        self.assertEqual(observed, ['aurora', False, True, 'closed'])
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['state'], 'stopped')

    def test_native_process_identity_is_read_only_and_distinguishes_current_process(self):
        if not desktop._windows_supported():
            self.skipTest('Windows process identity contract')
        identity = desktop._process_identity(desktop.os.getpid())
        self.assertTrue(identity)
        self.assertEqual(identity, desktop._process_identity(desktop.os.getpid()))

    def test_native_abi_configuration_never_creates_a_window(self):
        if not desktop._windows_supported():
            self.skipTest('Windows ABI contract')
        renderer = object.__new__(desktop._NativeRenderer)
        renderer.user = desktop.ctypes.WinDLL('user32', use_last_error=True)
        renderer.gdi = desktop.ctypes.WinDLL('gdi32', use_last_error=True)
        renderer.kernel = desktop.ctypes.WinDLL('kernel32', use_last_error=True)
        renderer._configure()
        self.assertEqual(renderer.user.CreateWindowExW.restype, desktop.ctypes.c_void_p)
        self.assertEqual(renderer.gdi.CreateCompatibleBitmap.restype, desktop.ctypes.c_void_p)

    def renderer_loop(self, pause_reason):
        renderer = object.__new__(desktop._NativeRenderer)
        renderer.user = SimpleNamespace(MonitorFromWindow=lambda *a: None, GetMonitorInfoW=lambda *a: None,
                       IsWindow=lambda *a: True, PeekMessageW=lambda *a: False)
        renderer.kernel = SimpleNamespace(GetSystemPowerStatus=lambda *a: None)
        renderer.parent, renderer.hwnd = 1, 2
        renderer.bounds = (0, 0, 1920, 1080)
        renderer._bounds = lambda: renderer.bounds
        renderer._pause_reason = lambda: pause_reason
        draws, published = [], []
        renderer._draw = draws.append
        stopped = iter([False, True])
        with patch.object(desktop.time, 'monotonic', return_value=100), patch.object(desktop.time, 'sleep') as sleep:
            renderer.run(lambda: next(stopped), lambda *args: published.append(args))
        return draws, published, sleep.call_args.args[0]

    def test_paused_renderer_pumps_control_without_drawing(self):
        draws, states, delay = self.renderer_loop('Pausado para economizar bateria.')
        self.assertEqual(draws, [])
        self.assertEqual(states[0][0], 'paused')
        self.assertGreaterEqual(delay, .2)

    def test_active_renderer_caps_frame_rate_at_twenty_four(self):
        draws, states, delay = self.renderer_loop('')
        self.assertEqual(len(draws), 1)
        self.assertEqual(states[0][0], 'running')
        self.assertGreaterEqual(delay, 1 / 24)

    def test_all_scenes_produce_time_varying_own_graphics(self):
        for scene in ('aurora', 'particles', 'waves'):
            renderer = object.__new__(desktop._NativeRenderer)
            renderer.scene, renderer.width, renderer.height = scene, 1280, 720
            renderer.dc, renderer.buffer = 1, 2
            renderer.gdi = SimpleNamespace(BitBlt=lambda *args: True)
            primitives = []
            renderer._fill = lambda color, **shape: primitives.append((color, shape))
            renderer._draw(0)
            first = list(primitives)
            primitives.clear()
            renderer._draw(3)
            self.assertTrue(first)
            self.assertNotEqual(first, primitives, scene)

    def test_hidden_workerw_is_not_reported_as_a_visible_desktop_host(self):
        renderer = object.__new__(desktop._NativeRenderer)
        renderer._enum_type = lambda fn: fn
        renderer.user = SimpleNamespace(
            FindWindowW=lambda *a: 1,
            FindWindowExW=lambda parent, after, name, title: 2 if name == 'SHELLDLL_DefView' else 3,
            SendMessageTimeoutW=lambda *a: 1,
            EnumWindows=lambda callback, data: callback(1, data),
            IsWindowVisible=lambda hwnd: False,
        )
        with self.assertRaises(OSError):
            renderer._desktop_host()


if __name__ == '__main__':
    unittest.main()
