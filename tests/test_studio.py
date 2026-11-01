import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeus_pc import server, windows_preferences as prefs
from zeus_pc.app_logging import StructuredLog
from zeus_pc.focus import FocusService
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.studio import Studio
from zeus_pc.transaction import TransactionEngine, build_default_registry
from zeus_pc.models import ProbeResult, Metric, Status


class StudioHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = {}
        def write(path, name, state):
            if state['exists']:
                self.store[path, name] = dict(state)
            else:
                self.store.pop((path, name), None)
        db = ZeusDatabase(root / 'test.db'); db.initialize()
        engine = TransactionEngine(db, build_default_registry())
        focus = FocusService(db, engine, lambda: {'supported': True})
        studio = Studio(focus)
        studio.state = lambda: {'supported': True, 'jobs': []}
        replacements = [('DB', db), ('TRANSACTIONS', engine), ('_FOCUS', focus), ('_STUDIO', studio),
                        ('APP_LOG', StructuredLog(root / 'test.jsonl'))]
        for name, value in replacements:
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        for name, value in {'read_value': lambda path, name: dict(self.store.get((path, name), {'exists': False})),
                            'write_value': write, 'notify': lambda: None, 'data_dir': lambda: root}.items():
            p = patch.object(prefs, name, value); p.start(); self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body, origin=None):
        client = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port)
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        client.request('POST', path, json.dumps(body), headers)
        response = client.getresponse()
        data = json.loads(response.read())
        status = response.status
        client.close()
        return status, data

    def test_settings_apply_and_restore_through_actual_http_handler(self):
        status, data = self.request('/api/studio/apply', {'items': [{'id': 'file_extensions', 'value': True}]})
        self.assertEqual(status, 200)
        self.assertEqual(data['run']['status'], 'verified')
        self.assertTrue(prefs.preference_state('file_extensions')['value'])
        status, restored = self.request('/api/focus/undo', {'report_id': data['run']['id']})
        self.assertEqual(status, 200)
        self.assertTrue(restored['run']['undone'])
        self.assertEqual(self.store, {})

    def test_invalid_later_item_prevents_every_write(self):
        status, _ = self.request('/api/studio/apply', {'items': [
            {'id': 'file_extensions', 'value': True}, {'id': 'shell', 'value': 'execute'}]})
        self.assertEqual(status, 400)
        self.assertEqual(self.store, {})

    def test_foreign_browser_origin_cannot_apply_settings(self):
        status, _ = self.request('/api/studio/apply', {'items': [{'id': 'game_mode', 'value': True}]}, origin='https://foreign.example')
        self.assertEqual(status, 403)
        self.assertEqual(self.store, {})

    def test_wallpaper_import_rejects_program_data(self):
        status, _ = self.request('/api/studio/wallpapers', {'filename': 'fake.png', 'data': 'TVpmb28='})
        self.assertEqual(status, 400)

    def test_vendor_telemetry_is_attached_to_matching_gpu(self):
        probe = ProbeResult('GPU', Status.OK, [Metric('adapters', [
            {'Name': 'Intel UHD', 'DriverVersion': 'intel'},
            {'Name': 'NVIDIA GeForce Example', 'DriverVersion': 'nvidia'}]),
            Metric('name', 'NVIDIA GeForce Example'), Metric('temperature_c', '65'),
            Metric('memory_total_mib', '8192')])
        studio = server.studio_service()
        with patch('zeus_pc.studio.gpu_probe', return_value=probe), patch.object(prefs, 'applications', return_value=[]):
            result = studio.gpu(refresh=True)
        self.assertNotIn('temperature_c', result['adapters'][0])
        self.assertEqual(result['adapters'][1]['temperature_c'], 65)
        self.assertEqual(result['adapters'][1]['vram_gb'], 8)


if __name__ == '__main__':
    unittest.main()
