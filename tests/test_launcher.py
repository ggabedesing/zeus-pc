"""Testes das funções testáveis do launcher (Increment 023).

Cobrem checagem de porta, detecção de instância Zeus existente e o caminho
de porta ocupada do main() (instância única -> 0; outro programa -> 1).
A janela Tk e a subida real do servidor são exercitadas no teste do EXE.
"""

from __future__ import annotations

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from zeus_pc import launcher, server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _StubHandler(BaseHTTPRequestHandler):
    """Responde /api/status como o Zeus; 404 no resto."""

    def do_GET(self) -> None:  # noqa: N802 (assinatura do http.server)
        if self.path == "/api/status":
            body = json.dumps({"version": "0.0.0-test"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:
        pass


class _Plain404Handler(BaseHTTPRequestHandler):
    """Porta aberta mas não é o Zeus."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def _serve(handler) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd: ThreadingHTTPServer, thread: threading.Thread) -> None:
    httpd.shutdown()
    thread.join(timeout=3)
    httpd.server_close()


class PortCheckTests(unittest.TestCase):
    def test_port_in_use_true_when_something_listens(self) -> None:
        httpd, thread = _serve(_Plain404Handler)
        try:
            self.assertTrue(
                launcher.port_in_use("127.0.0.1", httpd.server_address[1])
            )
        finally:
            _stop(httpd, thread)

    def test_port_in_use_false_when_free(self) -> None:
        port = _free_port()
        self.assertFalse(launcher.port_in_use("127.0.0.1", port))

    def test_server_url_is_loopback(self) -> None:
        self.assertEqual(launcher.server_url(8123), "http://127.0.0.1:8123")


class ServingCheckTests(unittest.TestCase):
    def test_detects_zeus_and_wait_returns_true(self) -> None:
        httpd, thread = _serve(_StubHandler)
        try:
            url = launcher.server_url(httpd.server_address[1])
            self.assertTrue(launcher.is_zeus_running(url))
            self.assertTrue(launcher.wait_until_serving(url, timeout=5))
        finally:
            _stop(httpd, thread)

    def test_plain_404_listener_is_not_zeus(self) -> None:
        httpd, thread = _serve(_Plain404Handler)
        try:
            url = launcher.server_url(httpd.server_address[1])
            self.assertFalse(launcher.is_zeus_running(url))
        finally:
            _stop(httpd, thread)

    def test_dead_port_is_not_zeus(self) -> None:
        url = launcher.server_url(_free_port())
        self.assertFalse(launcher.is_zeus_running(url, timeout=0.3))

    def test_wait_until_serving_gives_up(self) -> None:
        url = launcher.server_url(_free_port())
        self.assertFalse(launcher.wait_until_serving(url, timeout=0.8))


class MainPortBusyTests(unittest.TestCase):
    """main() com a porta ocupada: sem GUI, sem criar servidor novo."""

    def _run_main_with_port(self, port: int) -> tuple[int, list[str]]:
        fatals: list[str] = []
        with (
            patch.object(server, "PORT", port),
            patch.object(launcher, "_fatal", fatals.append),
            patch.object(launcher.webbrowser, "open"),
        ):
            code = launcher.main()
        return code, fatals

    def test_existing_zeus_opens_browser_and_exits_zero(self) -> None:
        httpd, thread = _serve(_StubHandler)
        try:
            code, fatals = self._run_main_with_port(httpd.server_address[1])
        finally:
            _stop(httpd, thread)
        self.assertEqual(code, 0)
        self.assertEqual(fatals, [])

    def test_foreign_program_on_port_exits_one_with_message(self) -> None:
        httpd, thread = _serve(_Plain404Handler)
        try:
            code, fatals = self._run_main_with_port(httpd.server_address[1])
        finally:
            _stop(httpd, thread)
        self.assertEqual(code, 1)
        self.assertEqual(len(fatals), 1)
        self.assertIn("porta", fatals[0].lower())


if __name__ == "__main__":
    unittest.main()
