import http.client
import json
import secrets
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

from zeus_pc import server
from zeus_pc.app_logging import StructuredLog
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.http_security import validate_request


class LocalAPISecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = ZeusDatabase(Path(cls.tmp.name) / "test.db")
        cls.db.initialize()
        cls.patches = [
            patch.object(server, "DB", cls.db),
            patch.object(server, "APP_LOG", StructuredLog(Path(cls.tmp.name) / "test.jsonl")),
            patch.object(server, "_sync_observer_runner"),
        ]
        for item in cls.patches:
            item.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.api_session_token = secrets.token_urlsafe(32)
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        for item in reversed(cls.patches):
            item.stop()
        cls.tmp.cleanup()

    def request(self, path="/api/config", method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        merged = {"X-Zeus-Session": self.httpd.api_session_token}
        merged.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_session_bootstrap_returns_ephemeral_token_without_auth(self):
        status, headers, body = self.request("/api/session", headers={"X-Zeus-Session": ""})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["session_token"], self.httpd.api_session_token)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_foreign_host_is_rejected_before_config_write(self):
        with patch.object(server, "save_config") as save:
            status, _, _ = self.request(method="POST", body='{}', headers={
                "Host": "outside.example", "Content-Type": "application/json",
            })
            self.assertEqual(status, 403)
            save.assert_not_called()

    def test_wrong_port_and_host_prefixes_are_rejected(self):
        for host in ["127.0.0.1:1", f"127.0.0.1.outside.example:{self.port}", "localhost"]:
            with self.subTest(host=host):
                self.assertEqual(self.request(headers={"Host": host})[0], 403)

    def test_localhost_alias_on_actual_port_is_accepted(self):
        self.assertEqual(self.request(headers={"Host": f"localhost:{self.port}"})[0], 200)

    def test_default_http_port_allows_browser_omission(self):
        for host in ["127.0.0.1", "localhost", "127.0.0.1:80", "localhost:80"]:
            with self.subTest(host=host):
                headers = Message()
                headers["Host"] = host
                headers["Origin"] = "http://" + host.replace(":80", "")
                headers["Referer"] = "http://" + host.replace(":80", "") + "/"
                handler = SimpleNamespace(headers=headers, path="/", server=SimpleNamespace(server_address=("127.0.0.1", 80)))
                validate_request(handler)

    def test_foreign_and_null_origins_are_rejected_even_with_token(self):
        for origin in ["https://outside.example", "null", f"http://127.0.0.1:{self.port + 1}"]:
            with self.subTest(origin=origin):
                self.assertEqual(self.request(headers={"Origin": origin})[0], 403)

    def test_origin_must_match_requested_host(self):
        self.assertEqual(self.request(headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)
        self.assertEqual(self.request(headers={"Origin": f"http://localhost:{self.port}"})[0], 403)

    def test_cross_site_bootstrap_and_framed_bootstrap_are_rejected(self):
        for headers in [{"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Dest": "iframe"},
                        {"Origin": "https://outside.example"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/session", headers=headers)[0], 403)

    def test_foreign_referer_is_rejected_when_origin_absent(self):
        self.assertEqual(self.request(headers={"Referer": "https://outside.example/"})[0], 403)

    def test_api_requires_valid_token_for_reads_and_writes(self):
        for token in ["", "incorrect", "é"]:
            with self.subTest(token=token):
                self.assertEqual(self.request(headers={"X-Zeus-Session": token})[0], 403)
                self.assertEqual(self.request(method="POST", body='{}', headers={
                    "X-Zeus-Session": token, "Content-Type": "application/json",
                })[0], 403)

    def test_rejected_transaction_request_never_prepares_action(self):
        with patch.object(server.TRANSACTIONS, "prepare") as prepare:
            status, _, _ = self.request("/api/transactions/prepare", method="POST", body='{}', headers={
                "X-Zeus-Session": "", "Content-Type": "application/json",
            })
            self.assertEqual(status, 403)
            prepare.assert_not_called()

    def test_valid_json_config_update_keeps_existing_flow(self):
        status, _, body = self.request(method="POST", body='{"selected_profile":"GAMING"}', headers={
            "Content-Type": "application/json; charset=utf-8",
            "Origin": f"http://127.0.0.1:{self.port}",
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["config"]["selected_profile"], "GAMING")

    def test_wrong_content_type_is_rejected(self):
        self.assertEqual(self.request(method="POST", body='{}', headers={"Content-Type": "text/plain"})[0], 415)

    def test_json_requires_object_and_valid_encoding(self):
        for body in [b'[]', b'null', b'1', b'"text"', b'{', b'\xff', b'{"x":NaN}']:
            with self.subTest(body=body):
                self.assertEqual(self.request(method="POST", body=body, headers={"Content-Type": "application/json"})[0], 400)

    def test_oversized_and_negative_content_lengths_are_rejected(self):
        for length, expected in [("65537", 413), ("-1", 400), ("nonsense", 400)]:
            with self.subTest(length=length):
                self.assertEqual(self.request(method="POST", body=b"", headers={
                    "Content-Type": "application/json", "Content-Length": length,
                })[0], expected)

    def test_transfer_encoding_is_rejected(self):
        self.assertEqual(self.request(method="POST", body=b"", headers={
            "Content-Type": "application/json", "Transfer-Encoding": "chunked",
        })[0], 400)

    def test_duplicate_critical_headers_are_rejected(self):
        values = {
            "Host": f"127.0.0.1:{self.port}",
            "Origin": f"http://127.0.0.1:{self.port}",
            "X-Zeus-Session": self.httpd.api_session_token,
            "Content-Length": "2", "Content-Type": "application/json",
        }
        for duplicated in values:
            with self.subTest(header=duplicated):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                try:
                    connection.putrequest("POST", "/api/config", skip_host=True)
                    for name, value in values.items():
                        connection.putheader(name, value)
                    connection.putheader(duplicated, values[duplicated])
                    connection.endheaders(b"{}")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                finally:
                    connection.close()

    def test_incomplete_body_times_out_without_dispatch(self):
        with patch.object(server, "REQUEST_TIMEOUT_SECONDS", 0.1), patch.object(server, "save_config") as save:
            self.assertEqual(self.request(method="POST", body=b"{", headers={
                "Content-Type": "application/json", "Content-Length": "10",
            })[0], 408)
            save.assert_not_called()

    def test_truncated_body_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("POST", "/api/config", body=b"{", headers={
                "X-Zeus-Session": self.httpd.api_session_token,
                "Content-Type": "application/json", "Content-Length": "10",
            })
            connection.sock.shutdown(socket.SHUT_WR)
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()

    def test_server_instances_have_distinct_random_session_tokens(self):
        first = server.LocalHTTPServer(("127.0.0.1", 0), server.Handler)
        second = server.LocalHTTPServer(("127.0.0.1", 0), server.Handler)
        try:
            self.assertGreaterEqual(len(first.api_session_token), 32)
            self.assertNotEqual(first.api_session_token, second.api_session_token)
        finally:
            first.server_close()
            second.server_close()

    def test_static_page_needs_no_token_and_cannot_be_framed(self):
        status, headers, body = self.request("/", headers={"X-Zeus-Session": ""})
        self.assertEqual(status, 200)
        self.assertIn(b"Zeus", body)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_report_download_works_with_session(self):
        scan_id = self.db.record_scan("GENERAL", [], [])
        status, headers, body = self.request(f"/api/report?scan_id={scan_id}")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"Relatorio", body)
