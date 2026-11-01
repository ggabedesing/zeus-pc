"""Disposable end-to-end fixture: real HTTP/database/cleanup, simulated Windows settings.

Run manually with PYTHONPATH=src; serves the production web build on :8001.
Only files inside its TemporaryDirectory can be removed by this fixture.
"""
import os
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeus_pc import server, transaction
from zeus_pc.app_logging import StructuredLog
from zeus_pc.cleanup import preview_temp
from zeus_pc.focus import FocusService
from zeus_pc.persistence import ZeusDatabase


def main():
    with tempfile.TemporaryDirectory(prefix='zeus-ui-test-') as directory:
        root = Path(directory)
        caches = root / 'cache'
        caches.mkdir()
        for i in range(3):
            path = caches / f'old-{i}.tmp'
            path.write_bytes(b'x' * 1024 * 1024)
            stamp = time.time() - 8 * 86400
            os.utime(path, (stamp, stamp))
        state = {'animations': True, 'startup': True}
        def entries():
            return [{'name': 'Aplicativo de teste', 'command': 'fixture.exe', 'enabled': state['startup']}]
        def collector():
            preview = preview_temp(caches)
            return {'supported': True, 'computer': 'AMBIENTE DE TESTE', 'cpu_name': 'CPU de teste',
                    'cpu_pct': 12, 'ram_used_pct': 40, 'ram_total_gb': 16, 'ram_available_gb': 9.6,
                    'disk_free_gb': 80, 'disk_total_gb': 256, 'disk_free_pct': 31,
                    'animations_enabled': state['animations'], 'startup': entries(), 'power_plans': [],
                    'temp_bytes': preview['bytes'], 'temp_count': len(preview['files']), '_temp_manifest': preview['files']}
        server.DB = ZeusDatabase(root / 'test.db')
        server.DB.initialize()
        server.APP_LOG = StructuredLog(root / 'test.jsonl')
        server.TRANSACTIONS = transaction.TransactionEngine(server.DB, transaction.build_default_registry())
        server._FOCUS = FocusService(server.DB, server.TRANSACTIONS, collector)
        with patch.object(transaction, 'read_animations', lambda: state['animations']), \
             patch.object(transaction, 'write_animations', lambda value: state.update(animations=value)), \
             patch.object(transaction, 'list_startup', entries), \
             patch.object(transaction, 'set_startup_enabled', lambda name, enabled: state.update(startup=enabled)), \
             patch('zeus_pc.focus.temp_root', lambda: caches):
            httpd = ThreadingHTTPServer(('127.0.0.1', 8001), server.Handler)
            print('Fixture Zeus em http://127.0.0.1:8001', flush=True)
            try:
                httpd.serve_forever()
            finally:
                httpd.server_close()


if __name__ == '__main__':
    main()
