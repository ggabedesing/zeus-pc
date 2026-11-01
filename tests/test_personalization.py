import copy
import ctypes
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc import personalization as p
from zeus_pc.transaction import ActionRegistry, TransactionError


def bmp():
    return (b'BM' + struct.pack('<IHHI', 58, 0, 0, 54)
            + struct.pack('<IiiHHIIiiII', 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0)
            + b'\x10\x20\x30\0')


class FakeWindows:
    supported = True

    def __init__(self):
        self.registry = {
            (p.PERSONALIZE, 'AppsUseLightTheme'): {'exists': True, 'type': 4, 'value': 1},
            (p.PERSONALIZE, 'EnableTransparency'): {'exists': True, 'type': 1, 'value': 'old'},
        }
        self.animations = dict(client_area=True, menu=False, menu_fade=False,
                               tooltip=True, tooltip_fade=False, selection_fade=True,
                               minimize_restore=False)
        self.wallpaper = 'C:\\Users\\Example\\original.bmp'
        self.fail_on = None

    def read_registry(self, path, name):
        return copy.deepcopy(self.registry.get((path, name), {'exists': False}))

    def write_registry(self, path, name, state):
        if state['exists']:
            self.registry[path, name] = copy.deepcopy(state)
        else:
            self.registry.pop((path, name), None)

    def read_animations(self):
        return dict(self.animations)

    def write_animations(self, values):
        self.animations = dict(values)
        if self.fail_on == 'animations':
            self.fail_on = None
            raise OSError('failed mid-apply')

    def read_wallpaper(self):
        return self.wallpaper

    def write_wallpaper(self, path):
        self.wallpaper = path
        self.write_registry(p.DESKTOP, 'Wallpaper', {'exists': True, 'type': 1, 'value': path})

    def notify(self):
        pass


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.native = FakeWindows()
        for target, value in [('_adapter', self.native), ('_data_dir', lambda: Path(self.temp.name))]:
            patcher = patch.object(p, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.registry = ActionRegistry()
        p.register_actions(self.registry)

    def action(self, name):
        return self.registry.get(name)

    def test_preset_apply_and_rollback_restore_absent_and_typed_values_exactly(self):
        original = copy.deepcopy(self.native.registry)
        animations = self.native.read_animations()
        action = self.action('SET_VISUAL_PRESET')
        args = action.validate_args({'preset_id': 'minimal'})
        before = action.capture_before(args)
        json.dumps(before)
        self.assertTrue(action.apply(args)['ok'])
        self.assertTrue(action.verify(args)['ok'])
        self.assertEqual(self.native.registry[p.PERSONALIZE, 'AppsUseLightTheme']['value'], 0)
        self.assertTrue(all(not enabled for enabled in self.native.animations.values()))
        self.assertTrue(action.rollback(before, action.rollback_preview(before))['ok'])
        self.assertEqual(self.native.registry, original)
        self.assertEqual(self.native.animations, animations)
        self.assertEqual(self.native.wallpaper, 'C:\\Users\\Example\\original.bmp')

    def test_restore_preview_is_an_executable_action_and_survives_registration(self):
        action = self.action('SET_VISUAL_PRESET')
        before = action.capture_before({'preset_id': 'aurora'})
        action.apply({'preset_id': 'aurora'})
        preview = action.rollback_preview(before)
        self.assertEqual(set(preview['args']), {'snapshot_id'})
        restore = self.action(preview['action_id'])
        args = restore.validate_args(preview['args'])
        restore.capture_before(args)
        self.assertTrue(restore.apply(args)['ok'])
        self.assertTrue(restore.verify(args)['ok'])

    def test_partial_apply_failure_self_restores_before_returning_failure(self):
        original = copy.deepcopy(self.native.registry)
        animations = dict(self.native.animations)
        self.native.fail_on = 'animations'
        result = self.action('SET_VISUAL_PRESET').apply({'preset_id': 'graphite'})
        self.assertFalse(result['ok'])
        self.assertTrue(result['restored'])
        self.assertEqual(self.native.registry, original)
        self.assertEqual(self.native.animations, animations)

    def test_failed_restore_verification_recovers_state_from_before_restore_attempt(self):
        preset = self.action('SET_VISUAL_PRESET')
        before = preset.capture_before({'preset_id': 'graphite'})
        preset.apply({'preset_id': 'graphite'})
        current = copy.deepcopy(self.native.registry)
        current_wallpaper = self.native.wallpaper
        original_write = self.native.write_registry
        ignored = False
        def ignore_once(path, name, state):
            nonlocal ignored
            if not ignored and name == 'AppsUseLightTheme' and state.get('value') == 1:
                ignored = True
                return
            original_write(path, name, state)
        restore = self.action('RESTORE_VISUAL_STATE')
        with patch.object(self.native, 'write_registry', ignore_once):
            result = restore.apply(preset.rollback_preview(before)['args'])
        self.assertFalse(result['ok'])
        self.assertTrue(result.get('restored'))
        self.assertEqual(self.native.registry, current)
        self.assertEqual(self.native.wallpaper, current_wallpaper)

    def test_import_is_content_addressed_and_wallpaper_rollback_restores_only_its_resource(self):
        image = bmp()
        imported = p.import_wallpaper(image)
        self.assertEqual(imported['id'], hashlib.sha256(image).hexdigest())
        self.assertEqual(p.import_wallpaper(image)['id'], imported['id'])
        self.assertEqual(p.wallpaper_asset(imported['id']).read_bytes(), image)
        action = self.action('SET_DESKTOP_WALLPAPER')
        args = action.validate_args({'asset_id': imported['id']})
        original = copy.deepcopy(self.native.registry)
        before = action.capture_before(args)
        action.apply(args)
        self.assertTrue(action.verify(args)['ok'])
        self.native.registry[p.PERSONALIZE, 'AppsUseLightTheme']['value'] = 0
        self.assertTrue(action.rollback(before, action.rollback_preview(before))['ok'])
        self.assertEqual(self.native.wallpaper, 'C:\\Users\\Example\\original.bmp')
        self.assertNotIn((p.DESKTOP, 'Wallpaper'), self.native.registry)
        self.assertEqual(self.native.registry[p.PERSONALIZE, 'AppsUseLightTheme']['value'], 0)

    def test_catalog_contains_managed_real_bitmaps_and_reports_native_capability_limits(self):
        data = p.catalog()
        self.assertEqual({x['id'] for x in data['presets']}, {'graphite', 'aurora', 'light', 'minimal'})
        for preset in data['presets']:
            self.assertTrue(p.wallpaper_asset(preset['wallpaper_id']).read_bytes().startswith(b'BM'))
            self.assertTrue(preset['colors']['accent'].startswith('#'))
        self.assertFalse(data['capabilities']['credential_layout'])
        self.assertFalse(data['capabilities']['lock_screen_wallpaper'])
        self.assertEqual(p.current_state()['theme'], 'mixed')

    def test_rejects_paths_unknown_ids_extra_args_and_modified_assets(self):
        action = self.action('SET_DESKTOP_WALLPAPER')
        for args in [{'asset_id': '../outside'}, {'asset_id': 'a' * 64},
                     {'asset_id': 'a' * 64, 'path': 'C:/anything.bmp'}]:
            with self.assertRaises((ValueError, TransactionError)):
                action.validate_args(args)
        imported = p.import_wallpaper(bmp())
        p.wallpaper_asset(imported['id']).write_bytes(bmp() + b'changed')
        with self.assertRaises((ValueError, TransactionError)):
            action.validate_args({'asset_id': imported['id']})
        with self.assertRaises((ValueError, TransactionError)):
            self.action('RESTORE_VISUAL_STATE').validate_args({'snapshot_id': '../../anything'})

    def test_import_rejects_invalid_truncated_and_oversized_images(self):
        for data in [b'not an image', b'BM', b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff\xd9',
                     b'x' * (20 * 1024 * 1024 + 1)]:
            with self.assertRaises(ValueError):
                p.import_wallpaper(data)

    def test_native_adapter_can_set_fade_preferences_with_master_effects_disabled(self):
        # Windows ignores fade writes while their respective master is off.
        values = {'menu': False, 'menu_fade': True, 'tooltip': False,
                  'tooltip_fade': True, 'client_area': True,
                  'selection_fade': True, 'minimize_restore': True}
        operations = {0x1043: 'client_area', 0x1003: 'menu', 0x1013: 'menu_fade',
                      0x1017: 'tooltip', 0x1019: 'tooltip_fade', 0x1015: 'selection_fade'}
        def spi(operation, param=0, value=None, flags=0):
            if operation == 0x0049:
                info = ctypes.cast(value, ctypes.POINTER(p._AnimationInfo)).contents
                values['minimize_restore'] = bool(info.iMinAnimate)
                return
            name = operations[operation]
            if name == 'menu_fade' and not values['menu']:
                return
            if name == 'tooltip_fade' and not values['tooltip']:
                return
            values[name] = bool(value.value)
        adapter = p.NativeWindowsAdapter()
        with patch.object(adapter, '_spi', spi):
            adapter.write_animations({name: False for name in values})
        self.assertEqual(values, {'menu': False, 'menu_fade': False, 'tooltip': False,
                                  'tooltip_fade': False, 'client_area': False,
                                  'selection_fade': False, 'minimize_restore': False})


if __name__ == '__main__':
    unittest.main()
