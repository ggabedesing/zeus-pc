import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeus_pc import server
from zeus_pc.app_logging import StructuredLog
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.server import host_is_loopback, origin_is_loopback
from zeus_pc.transaction import TransactionEngine

UNSET = object()


class GuardHelperTests(unittest.TestCase):
    def test_host_allows_loopback_and_rejects_everything_else(self):
        for host in ("127.0.0.1", "127.0.0.1:8000", "localhost:8000", "[::1]:8000"):
            self.assertTrue(host_is_loopback(host), host)
        for host in (
            "evil.example.com",
            "evil.example.com:8000",
            "127.0.0.1.evil.com",
            "0.0.0.0:8000",
            "192.168.0.10:8000",
            "",
            None,
        ):
            self.assertFalse(host_is_loopback(host), host)

    def test_origin_requires_loopback_and_matching_port(self):
        self.assertTrue(origin_is_loopback(None, 8000))
        self.assertTrue(origin_is_loopback("", 8000))
        self.assertTrue(origin_is_loopback("http://127.0.0.1:8000", 8000))
        self.assertTrue(origin_is_loopback("http://localhost:8000", 8000))
        for bad in (
            "https://evil.example.com",
            "http://evil.example.com:8000",
            "http://127.0.0.1:9999",
            "null",
            "file://",
            "http://192.168.0.10:8000",
        ):
            self.assertFalse(origin_is_loopback(bad, 8000), bad)


class ApiGuardTests(unittest.TestCase):
    """Real Handler over HTTP: the guards as a browser would hit them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db = ZeusDatabase(root / "zeus.db")
        self.db.initialize()
        self.app_log = StructuredLog(root / "app.jsonl", max_entries=200)

        for name, value in (
            ("DB", self.db),
            ("TRANSACTIONS", TransactionEngine(self.db)),
            ("APP_LOG", self.app_log),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(
        self,
        method: str,
        path: str,
        *,
        host: str | None = None,
        origin: object = UNSET,
        content_type: str | None = None,
        body: bytes | None = None,
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=5
        )
        headers: dict[str, str] = {}
        if host is not None:
            headers["Host"] = host
        if origin is not UNSET:
            headers["Origin"] = origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    # --- Host (DNS rebinding) ------------------------------------------

    def test_cross_domain_host_is_rejected_on_get_and_post(self):
        status, _, payload = self.request(
            "GET", "/api/status", host="evil.example.com:8000"
        )
        self.assertEqual(status, 403)
        self.assertIn("Host negado", json.loads(payload)["message"])

        status, _, payload = self.request(
            "POST",
            "/api/brain/invoke",
            host="evil.example.com:8000",
            content_type="application/json",
            body=b"{}",
        )
        self.assertEqual(status, 403)
        self.assertIn("Host negado", json.loads(payload)["message"])

    def test_loopback_hosts_are_served(self):
        for host in (None, "localhost:%d" % self.port, "[::1]:%d" % self.port):
            status, headers, payload = self.request(
                "GET", "/api/brain/models", host=host
            )
            self.assertEqual(status, 200, host)
            self.assertIn("count", json.loads(payload))
            # nunca vaza CORS: um site nao pode ler a resposta
            self.assertNotIn("Access-Control-Allow-Origin", dict(headers))

    def test_host_guard_applies_to_static_files_too(self):
        status, _, _ = self.request(
            "GET", "/", host="attacker.example.com"
        )
        self.assertEqual(status, 403)

    # --- Origin (CSRF de navegador) ------------------------------------

    def test_cross_site_origin_is_rejected_on_mutations(self):
        for origin in (
            "https://evil.example.com",
            "http://evil.example.com:8000",
            "null",
            "http://127.0.0.1:%d" % (self.port + 1),
        ):
            status, _, payload = self.request(
                "POST",
                "/api/brain/invoke",
                origin=origin,
                content_type="application/json",
                body=b'{"task":"CHAT","prompt":"ping"}',
            )
            self.assertEqual(status, 403, origin)
            self.assertIn("negada", json.loads(payload)["message"])

    def test_same_origin_and_non_browser_requests_pass_the_origin_guard(self):
        # navegador same-origin
        status, _, _ = self.request(
            "POST",
            "/api/rota-que-nao-existe",
            origin="http://127.0.0.1:%d" % self.port,
            content_type="application/json",
            body=b"{}",
        )
        self.assertEqual(status, 404)

        # cliente sem navegador (sem Origin)
        status, _, _ = self.request(
            "POST",
            "/api/rota-que-nao-existe",
            content_type="application/json",
            body=b"{}",
        )
        self.assertEqual(status, 404)

    def test_get_is_not_blocked_by_origin_header(self):
        status, _, _ = self.request(
            "GET", "/api/brain/models", origin="https://evil.example.com"
        )
        self.assertEqual(status, 200)

    # --- Content-Type (no-cors text/plain) -----------------------------

    def test_simple_content_types_are_rejected_on_mutations(self):
        for content_type in (
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=x",
        ):
            status, _, payload = self.request(
                "POST",
                "/api/brain/invoke",
                content_type=content_type,
                body=b'{"task":"CHAT"}',
            )
            self.assertEqual(status, 415, content_type)
            self.assertIn("application/json", json.loads(payload)["message"])

    def test_json_content_type_reaches_normal_routing(self):
        status, _, _ = self.request(
            "POST",
            "/api/brain/invoke",
            content_type="application/json; charset=utf-8",
            body=b'{"task":"TELEPORT","prompt":"x"}',
        )
        # passou pelos guards; a camada de dominio responde (409 BrainError)
        self.assertEqual(status, 409)

    # --- 404 customizado ------------------------------------------------

    def test_unknown_page_serves_custom_404(self):
        status, headers, payload = self.request("GET", "/pagina-que-nao-existe")
        self.assertEqual(status, 404)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = payload.decode("utf-8")
        self.assertIn("404", html)
        self.assertIn("Voltar ao início", html)

    def test_api_404_stays_json(self):
        status, headers, payload = self.request(
            "GET", "/api/nao-existe"
        )
        self.assertEqual(status, 404)
        self.assertIn("json", headers.get("Content-Type", ""))
        self.assertIn("error", json.loads(payload))


class PersistenceConcurrencyTests(unittest.TestCase):
    def test_wal_and_busy_timeout_are_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            with db.connect() as connection:
                journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                busy = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(journal.lower(), "wal")
            self.assertEqual(int(busy), 5000)

    def test_reads_do_not_reinitialize_the_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            with patch.object(
                db, "initialize", side_effect=AssertionError("re-init")
            ):
                self.assertIsNone(db.get_model_install("X"))
                self.assertEqual(db.list_model_installs(), [])
                self.assertEqual(db.benchmark_scores(), {})
                self.assertEqual(db.latest_benchmarks(), [])
                self.assertIsNone(db.get_transaction("missing"))
                self.assertEqual(db.recent_transactions(), [])

    def test_fresh_database_reads_work_without_explicit_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            self.assertIsNone(db.get_model_install("X"))
            self.assertEqual(db.benchmark_scores(), {})
            health = db.health()
            self.assertGreaterEqual(health.schema_version, 4)


if __name__ == "__main__":
    unittest.main()
