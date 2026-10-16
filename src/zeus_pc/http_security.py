"""Browser boundary for the loopback API; not an OS-user authentication system."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

SESSION_HEADER = "X-Zeus-Session"
MAX_BODY_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 10


class RequestRejected(ValueError):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def _single_header(handler: BaseHTTPRequestHandler, name: str) -> str | None:
    values = handler.headers.get_all(name, [])
    if len(values) > 1:
        raise RequestRejected(f"Cabecalho {name} duplicado.")
    return values[0] if values else None


def validate_request(handler: BaseHTTPRequestHandler, *, bootstrap: bool = False) -> None:
    host = _single_header(handler, "Host")
    port = handler.server.server_address[1]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if port == 80:
        allowed_hosts.update({"127.0.0.1", "localhost"})
    if host not in allowed_hosts:
        raise RequestRejected("Host local invalido.", HTTPStatus.FORBIDDEN)

    target = urlsplit(handler.path)
    if target.scheme or target.netloc or not handler.path.startswith("/"):
        raise RequestRejected("Destino de requisicao invalido.")

    expected_origins = {f"http://{host}"}
    if port == 80:
        hostname = host.removesuffix(":80")
        expected_origins.update({f"http://{hostname}", f"http://{hostname}:80"})
    origin = _single_header(handler, "Origin")
    if origin is not None and origin not in expected_origins:
        raise RequestRejected("Origem da requisicao bloqueada.", HTTPStatus.FORBIDDEN)

    referer = _single_header(handler, "Referer")
    if referer is not None:
        parsed = urlsplit(referer)
        if f"{parsed.scheme}://{parsed.netloc}" not in expected_origins:
            raise RequestRejected("Referencia da requisicao bloqueada.", HTTPStatus.FORBIDDEN)

    if _single_header(handler, "Sec-Fetch-Site") == "cross-site":
        raise RequestRejected("Requisicao entre sites bloqueada.", HTTPStatus.FORBIDDEN)
    if _single_header(handler, "Sec-Fetch-Dest") in {"iframe", "frame", "object", "embed"}:
        raise RequestRejected("Carregamento incorporado bloqueado.", HTTPStatus.FORBIDDEN)

    if target.path.startswith("/api/") and not bootstrap:
        token = _single_header(handler, SESSION_HEADER) or ""
        expected = getattr(handler.server, "api_session_token", "")
        # Bytes also permit a clean rejection of malformed non-ASCII headers.
        if not expected or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
            raise RequestRejected("Sessao local invalida. Recarregue a interface.", HTTPStatus.FORBIDDEN)


def read_json_object(handler: BaseHTTPRequestHandler) -> dict:
    if handler.headers.get_all("Transfer-Encoding"):
        raise RequestRejected("Transfer-Encoding nao suportado.")
    content_type = _single_header(handler, "Content-Type")
    if not content_type or content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise RequestRejected("Content-Type deve ser application/json.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    if handler.headers.get_content_charset("utf-8").lower() != "utf-8":
        raise RequestRejected("JSON deve usar UTF-8.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    raw_length = _single_header(handler, "Content-Length")
    if raw_length is None:
        raise RequestRejected("Content-Length obrigatorio.", HTTPStatus.LENGTH_REQUIRED)
    if not raw_length.isascii() or not raw_length.isdigit():
        raise RequestRejected("Content-Length invalido.")
    length = int(raw_length)
    if length > MAX_BODY_BYTES:
        raise RequestRejected("Payload muito grande.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    if length == 0:
        raise RequestRejected("Corpo JSON obrigatorio.")
    try:
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise RequestRejected("Corpo JSON incompleto.")
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError) as exc:
        raise RequestRejected("JSON invalido.") from exc
    except TimeoutError as exc:
        raise RequestRejected("Tempo esgotado ao receber JSON.", HTTPStatus.REQUEST_TIMEOUT) from exc
    if not isinstance(payload, dict):
        raise RequestRejected("O corpo JSON deve ser um objeto.")
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Constante JSON invalida: {value}")
