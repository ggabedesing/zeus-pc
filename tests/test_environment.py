import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc import environment as env


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = env.EnvironmentStore(self.root / 'environment')

    def test_defaults_and_partial_changes_preserve_notes_and_positions(self):
        first = self.store.read()
        self.assertEqual(first['theme'], 'aurora')
        self.assertTrue(first['widgets']['clock'])
        saved = self.store.update({'notes': 'Só neste PC', 'positions': {'clock': {'x': .4, 'y': .2}}})
        changed = self.store.update({'accent': '#abcdef', 'widgets': {'system': False}})
        self.assertEqual(changed['notes'], saved['notes'])
        self.assertEqual(changed['positions']['clock']['x'], .4)
        self.assertFalse(changed['widgets']['system'])
        self.assertTrue(changed['widgets']['clock'])
        self.assertGreater(changed['revision'], first['revision'])

    def test_invalid_config_never_overwrites_last_good_value(self):
        before = self.store.update({'theme': 'graphite'})
        for value in [{'theme': '../shell'}, {'motion': 1}, {'opacity': 2},
                      {'accent': 'url(secret)'}, {'widgets': {'shell': True}},
                      {'positions': {'clock': {'x': float('nan'), 'y': .2}}},
                      {'pinned': ['C:\\evil.exe']}, {'notes': 'a' * 4001}, {'execute': 'cmd'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.update(value)
        self.assertEqual(self.store.read(), before)

    def test_corrupt_file_is_reported_and_never_overwritten_by_read(self):
        self.store.directory.mkdir()
        path = self.store.directory / 'config.json'
        path.write_text('broken')
        with self.assertRaises(ValueError):
            self.store.read()
        self.assertEqual(path.read_text(), 'broken')

    def test_widget_geometry_stays_inside_work_area_and_respects_saved_position(self):
        config = self.store.read()
        for area in [(0, 0, 1920, 1040), (-1280, 30, 0, 720), (0, 0, 800, 540)]:
            boxes = env.widget_geometry(config, area)
            for name, (x, y, w, h) in boxes.items():
                self.assertGreaterEqual(x, area[0], name)
                self.assertGreaterEqual(y, area[1], name)
                self.assertLessEqual(x + w, area[2], name)
                self.assertLessEqual(y + h, area[3], name)
        config['positions']['clock'] = {'x': 1, 'y': 1}
        x, y, w, h = env.widget_geometry(config, (0, 0, 1000, 700))['clock']
        self.assertEqual((x+w, y+h), (1000, 700))

    def test_launcher_search_matches_accents_and_never_runs_unknown_or_removed_files(self):
        programs = self.root / 'Programs'; programs.mkdir()
        link = programs / 'Edição de vídeo.lnk'; link.touch()
        app = programs / 'Game.lnk'; app.touch()
        catalog = env.ApplicationCatalog([programs])
        items = catalog.list()
        found = env.search_applications(items, 'edicao video')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['name'], 'Edição de vídeo')
        with patch.object(env, '_open_file') as launch:
            catalog.launch(found[0]['id'])
            launch.assert_called_once_with(str(link.resolve()))
            with self.assertRaises(ValueError):
                catalog.launch('C:\\Windows\\System32\\cmd.exe')
            link.unlink()
            with self.assertRaises(ValueError):
                catalog.launch(found[0]['id'])

    def test_catalog_ignores_executables_and_outside_symlink_targets(self):
        programs = self.root / 'Programs'; programs.mkdir()
        (programs / 'not-a-link.exe').touch()
        nested = programs / 'Nested'; nested.mkdir()
        (nested / 'App.lnk').touch()
        self.assertEqual([i['name'] for i in env.ApplicationCatalog([programs]).list()], ['App'])


if __name__ == '__main__':
    unittest.main()
