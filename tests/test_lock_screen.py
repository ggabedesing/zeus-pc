import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zeus_pc import lock_screen as lock
from zeus_pc.transaction import ActionRegistry


class FakeLockScreen:
    supported = True
    def __init__(self):
        self.image = b'original-image'
    def read(self):
        return self.image
    def set(self, path):
        self.image = Path(path).read_bytes()
        return hashlib.sha256(self.image).hexdigest()


class LockScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = {}
        def import_image(data):
            id = hashlib.sha256(data).hexdigest()
            path = self.root / (id + '.jpg')
            path.write_bytes(data)
            self.assets[id] = path
            return {'id': id}
        self.import_image = import_image
        self.adapter = FakeLockScreen()
        for name, value in {'_adapter': self.adapter, 'data_dir': lambda: self.root,
                            'import_wallpaper': import_image, 'wallpaper_asset': lambda id: self.assets[id]}.items():
            p = patch.object(lock, name, value, create=True); p.start(); self.addCleanup(p.stop)
        self.registry = ActionRegistry()
        lock.register_actions(self.registry)

    def test_apply_verify_and_restore_actual_previous_image(self):
        asset = self.import_image(b'new-image')
        action = self.registry.get('SET_LOCK_SCREEN_WALLPAPER')
        args = action.validate_args({'asset_id': asset['id']})
        before = action.capture_before(args)
        self.assertTrue(action.apply(args)['ok'])
        self.assertTrue(action.verify(args)['ok'])
        self.assertEqual(self.adapter.image, b'new-image')
        self.assertTrue(action.rollback(before, {})['ok'])
        self.assertEqual(self.adapter.image, b'original-image')

    def test_changed_image_does_not_verify_as_original_request(self):
        asset = self.import_image(b'new-image')
        action = self.registry.get('SET_LOCK_SCREEN_WALLPAPER')
        args = action.validate_args({'asset_id': asset['id']})
        action.capture_before(args); action.apply(args)
        self.adapter.image = b'external-change'
        self.assertFalse(action.verify(args)['ok'])

    def test_client_cannot_supply_arbitrary_native_path(self):
        action = self.registry.get('SET_LOCK_SCREEN_WALLPAPER')
        with self.assertRaises(RuntimeError):
            action.validate_args({'path': 'C:\\outside.jpg'})


if __name__ == '__main__':
    unittest.main()
