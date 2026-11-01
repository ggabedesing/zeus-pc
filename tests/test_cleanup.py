import os
import tempfile
import time
import unittest
from pathlib import Path

from zeus_pc.cleanup import preview_temp, clean_temp


class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def old(self, name, data=b'old cache'):
        target = self.root / name
        target.write_bytes(data)
        stamp = time.time() - 8 * 86400
        os.utime(target, (stamp, stamp))
        return target

    def test_cleanup_deletes_only_previewed_old_files_and_counts_actual_bytes(self):
        old = self.old('old.tmp', b'x' * 100)
        fresh = self.root / 'working.tmp'
        fresh.write_bytes(b'keep')
        preview = preview_temp(self.root)
        self.assertEqual(preview['bytes'], 100)
        result = clean_temp(self.root, preview['files'])
        self.assertEqual(result['freed_bytes'], 100)
        self.assertEqual(result['deleted'], 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_file_changed_after_preview_is_not_deleted(self):
        changed = self.old('changed.tmp')
        preview = preview_temp(self.root)
        changed.write_bytes(b'now in use')
        result = clean_temp(self.root, preview['files'])
        self.assertTrue(changed.exists())
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(result['skipped'], 1)

    def test_manifest_cannot_escape_temp_root(self):
        other = self.root.parent / 'zeus-test-outside.txt'
        other.write_bytes(b'keep')
        self.addCleanup(lambda: other.unlink(missing_ok=True))
        preview = preview_temp(self.root)
        preview['files'].append({'path': str(other), 'size': 4, 'mtime_ns': 0, 'ino': 0, 'dev': 0})
        result = clean_temp(self.root, preview['files'])
        self.assertTrue(other.exists())
        self.assertEqual(result['freed_bytes'], 0)

    def test_symlinks_are_not_followed(self):
        target = self.old('target.tmp')
        try:
            (self.root / 'link').symlink_to(target)
        except OSError:
            self.skipTest('Symlinks need Windows developer mode.')
        preview = preview_temp(self.root)
        self.assertEqual(len(preview['files']), 1)
