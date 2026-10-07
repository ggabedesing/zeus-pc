from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import BUILD, __version__
from .actions import list_power_plans, set_power_plan
from .engine import build_recommendations
from .observer import collect_sample
from .probes import collect_all

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
HOST = "127.0.0.1"
PORT = int(os.environ.get("ZEUS_PORT", "8000"))


def dumps(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ZeusPC/0.1"

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 65536:
            raise ValueError("Payload muito grande.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self.send_json({
                "version": __version__,
                "build": BUILD,
                "execution_mode": "Restricted",
                "enabled_actions": ["SET_ACTIVE_POWER_PLAN"],
                "host": HOST,
            })
        if path == "/api/scan":
            snapshot = collect_all()
            profile = parse_qs(parsed.query).get("profile", ["GENERAL"])[0]
            recommendations = build_recommendations(snapshot, profile)
            return self.send_json({
                "snapshot": [item.to_dict() for item in snapshot],
                "profile": profile.upper(),
                "recommendations": [item.to_dict() for item in recommendations],
            })
        if path == "/api/observer/sample":
            return self.send_json(collect_sample().to_dict())
        if path == "/api/power-plans":
            return self.send_json({"plans": list_power_plans()})
        if path.startswith("/api/"):
            return self.send_json({"error": "Endpoint nao encontrado."}, 404)

        if path == "/":
            path = "/index.html"
        target = (WEB / path.lstrip("/")).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve():
            return self.send_error(403)
        if not target.exists() or not target.is_file():
            return self.send_error(404)
        data = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/actions/power-plan":
            return self.send_json({"error": "Acao bloqueada."}, 404)
        try:
            payload = self.read_json()
            guid = str(payload.get("guid") or "")
            result = set_power_plan(guid)
            return self.send_json({"ok": result.ok, "message": result.message}, 200 if result.ok else 409)
        except Exception as exc:
            return self.send_json({"ok": False, "message": str(exc)}, 400)


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    print(f"Zeus PC {__version__} ({BUILD})")
    print(f"http://{HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
