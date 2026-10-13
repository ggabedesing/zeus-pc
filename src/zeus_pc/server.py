from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import BUILD, __version__
from .actions import list_power_plans
from .app_logging import StructuredLog
from .config import CONFIG_SCHEMA_VERSION, load_config, save_config
from .engine import build_recommendations
from .observer import OBSERVER, OBSERVER_RUNNER
from .optimization import build_optimization_plan, snapshot_from_payload
from .persistence import ZeusDatabase
from .process_observer import PROCESS_OBSERVER
from .probes import collect_all
from .report import build_report_html
from .transaction import TransactionEngine, TransactionError, TransactionState

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
DATA = ROOT / "data"
DB = ZeusDatabase(DATA / "zeus.db")
APP_LOG = StructuredLog(DATA / "zeus-app.jsonl", max_entries=2000)
TRANSACTIONS = TransactionEngine(DB)
HOST = "127.0.0.1"
PORT = int(os.environ.get("ZEUS_PORT", "8000"))


def dumps(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def run_scan(profile: str, database: ZeusDatabase = DB, app_log: StructuredLog = APP_LOG) -> dict:
    snapshot = collect_all()
    recommendations = build_recommendations(snapshot, profile)
    snapshot_payload = [item.to_dict() for item in snapshot]
    recommendation_payload = [item.to_dict() for item in recommendations]
    optimization_plan = build_optimization_plan(
        snapshot,
        profile,
        observer_baseline=OBSERVER.baseline(60),
        process_snapshot=PROCESS_OBSERVER.latest(),
    )

    database.initialize()
    scan_id = database.record_scan(
        profile,
        snapshot_payload,
        recommendation_payload,
    )
    app_log.write(
        "scan_completed",
        scan_id=scan_id,
        profile=profile.upper(),
        domain_count=len(snapshot_payload),
        recommendation_count=len(recommendation_payload),
        optimization_plan_id=optimization_plan.plan_id,
    )
    return {
        "scan_id": scan_id,
        "snapshot": snapshot_payload,
        "profile": optimization_plan.profile,
        "recommendations": recommendation_payload,
        "optimization_plan": optimization_plan.to_dict(),
    }


def _parse_limit(query: dict[str, list[str]], default: int) -> int:
    raw = query.get("limit", [str(default)])[0]
    return int(raw)


def _sync_observer_runner(enabled: bool) -> None:
    if enabled:
        if OBSERVER_RUNNER.start():
            APP_LOG.write("observer_started")
    else:
        if OBSERVER_RUNNER.stop():
            APP_LOG.write("observer_stopped")


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
        query = parse_qs(parsed.query)
        config = load_config(DB)

        if path == "/api/status":
            health = DB.health()
            return self.send_json({
                "version": __version__,
                "build": BUILD,
                "execution_mode": "TransactionExecution",
                "enabled_actions": TRANSACTIONS.registry.executable_ids(),
                "preparable_actions": TRANSACTIONS.registry.ids(),
                "host": HOST,
                "persistence": {
                    "schema_version": health.schema_version,
                    "writable": health.writable,
                },
                "config": {
                    "schema_version": CONFIG_SCHEMA_VERSION,
                    "selected_profile": config.selected_profile,
                },
                "observer": {
                    "enabled": config.observer_enabled,
                    "buffer_capacity": OBSERVER.max_samples,
                    **OBSERVER_RUNNER.status(),
                },
            })
        if path == "/api/config":
            return self.send_json({"config": config.to_dict()})
        if path == "/api/scan":
            profile = query.get("profile", [config.selected_profile])[0]
            return self.send_json(run_scan(profile))
        if path == "/api/history":
            try:
                limit = _parse_limit(query, config.history_limit)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            return self.send_json({"scans": DB.recent_scans(limit)})
        if path.startswith("/api/scan/") and path[len("/api/scan/"):].isdigit():
            scan = DB.get_scan(int(path[len("/api/scan/"):]))
            if scan is None:
                return self.send_json({"error": "Analise nao encontrada."}, 404)
            return self.send_json(scan)
        if path == "/api/report":
            scan_id = query.get("scan_id", [None])[0]
            if scan_id is None:
                history = DB.recent_scans(1)
                scan = history[0] if history else None
            else:
                scan = DB.get_scan(scan_id)
            if scan is None:
                return self.send_json({"error": "Nenhuma analise encontrada para gerar o relatorio."}, 404)
            body = build_report_html(scan, version=__version__, build=BUILD).encode("utf-8")
            filename = f"zeus-pc-relatorio-{scan.get('id', 'atual')}.html"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f"attachment; filename={filename}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/optimization/plan":
            history = DB.recent_scans(1)
            if not history:
                profile = query.get(
                    "profile",
                    [config.selected_profile],
                )[0]
                scan = run_scan(profile)
                return self.send_json({
                    "source_scan_id": scan["scan_id"],
                    "source_scan_created_at": None,
                    "plan": scan["optimization_plan"],
                })

            latest = history[0]
            profile = query.get(
                "profile",
                [latest["profile"]],
            )[0]
            session_id = query.get("session_id", [None])[0]
            snapshot = snapshot_from_payload(latest["snapshot"])
            plan = build_optimization_plan(
                snapshot,
                profile,
                observer_baseline=OBSERVER.baseline(
                    60,
                    session_id=session_id,
                ),
                process_snapshot=PROCESS_OBSERVER.latest(
                    session_id=session_id,
                ),
            )
            return self.send_json({
                "source_scan_id": latest["id"],
                "source_scan_created_at": latest["created_at"],
                "session_id": session_id,
                "plan": plan.to_dict(),
            })
        if path == "/api/observer/status":
            return self.send_json({
                "enabled": config.observer_enabled,
                "buffer_capacity": OBSERVER.max_samples,
                **OBSERVER_RUNNER.status(),
            })
        if path == "/api/observer/sample":
            if not config.observer_enabled:
                return self.send_json({"error": "Observer desativado."}, 409)
            return self.send_json(OBSERVER_RUNNER.sample_once().to_dict())
        if path == "/api/observer/history":
            try:
                limit = _parse_limit(query, 60)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            session_id = query.get("session_id", [None])[0]
            return self.send_json({
                "samples": [
                    item.to_dict()
                    for item in OBSERVER.recent(limit, session_id=session_id)
                ]
            })
        if path == "/api/observer/baseline":
            try:
                limit = _parse_limit(query, 60)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            session_id = query.get("session_id", [None])[0]
            return self.send_json(
                OBSERVER.baseline(limit, session_id=session_id)
            )
        if path == "/api/observer/processes":
            try:
                limit = _parse_limit(query, 5)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            session_id = query.get("session_id", [None])[0]
            latest = PROCESS_OBSERVER.latest(session_id=session_id)
            if latest is None and config.observer_enabled:
                latest = OBSERVER_RUNNER.collect_process_context(force=True)
                if latest is not None and session_id and latest.session_id != session_id:
                    latest = PROCESS_OBSERVER.latest(session_id=session_id)
            return self.send_json({
                "latest": latest.to_dict() if latest is not None else None,
                "snapshots": [
                    item.to_dict()
                    for item in PROCESS_OBSERVER.recent(
                        limit,
                        session_id=session_id,
                    )
                ],
            })
        if path == "/api/observer/sessions":
            try:
                limit = _parse_limit(query, 10)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            return self.send_json({
                "sessions": [
                    item.to_dict()
                    for item in OBSERVER_RUNNER.recent_sessions(limit)
                ]
            })
        if path == "/api/transactions":
            try:
                limit = _parse_limit(query, 20)
            except ValueError:
                return self.send_json({"error": "limit invalido."}, 400)
            return self.send_json({
                "transactions": TRANSACTIONS.recent(limit),
                "execution_enabled": True,
            })
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
        try:
            payload = self.read_json()

            if path == "/api/config":
                config = save_config(DB, payload)
                _sync_observer_runner(config.observer_enabled)
                APP_LOG.write(
                    "config_updated",
                    selected_profile=config.selected_profile,
                    history_limit=config.history_limit,
                    observer_enabled=config.observer_enabled,
                )
                return self.send_json({"config": config.to_dict()})

            if path == "/api/observer/session/start":
                config = load_config(DB)
                if not config.observer_enabled:
                    return self.send_json({"error": "Observer desativado."}, 409)
                session, created = OBSERVER_RUNNER.start_session(
                    label=str(payload.get("label") or ""),
                    mode=str(payload.get("mode") or "AUTO"),
                )
                if created:
                    APP_LOG.write(
                        "observer_session_started",
                        session_id=session.id,
                        label=session.label,
                        mode=session.mode,
                    )
                return self.send_json({
                    "created": created,
                    "session": session.to_dict(),
                })

            if path == "/api/observer/session/stop":
                session = OBSERVER_RUNNER.stop_session()
                if session is None:
                    return self.send_json({"error": "Nenhuma sessao ativa."}, 409)
                APP_LOG.write(
                    "observer_session_stopped",
                    session_id=session.id,
                    label=session.label,
                    mode=session.mode,
                )
                return self.send_json({"session": session.to_dict()})

            if path == "/api/transactions/prepare":
                args = payload.get("args")
                if not isinstance(args, dict):
                    raise ValueError("args deve ser um objeto.")
                prepared = TRANSACTIONS.prepare(
                    str(payload.get("action_id") or ""),
                    args,
                )
                APP_LOG.write(
                    "transaction_prepared",
                    transaction_id=prepared.transaction_id,
                    action_id=prepared.action_id,
                )
                return self.send_json(
                    {"transaction": prepared.to_dict()},
                    201,
                )

            if path == "/api/transactions/consent":
                transaction = TRANSACTIONS.consume_consent(
                    str(payload.get("transaction_id") or ""),
                    str(payload.get("consent_token") or ""),
                )
                APP_LOG.write(
                    "transaction_consented",
                    transaction_id=transaction["transaction_id"],
                    action_id=transaction["action_id"],
                )
                return self.send_json({"transaction": transaction})

            if path == "/api/transactions/execute":
                transaction_id = str(payload.get("transaction_id") or "")
                try:
                    executed = TRANSACTIONS.execute(transaction_id)
                except TransactionError as exc:
                    APP_LOG.write(
                        "transaction_execution_rejected",
                        level="WARN",
                        transaction_id=transaction_id,
                        reason=str(exc),
                    )
                    return self.send_json({"ok": False, "message": str(exc)}, 409)
                verified = executed["state"] == TransactionState.VERIFIED.value
                APP_LOG.write(
                    "transaction_executed" if verified else "transaction_execution_failed",
                    level="INFO" if verified else "WARN",
                    transaction_id=executed["transaction_id"],
                    action_id=executed["action_id"],
                    state=executed["state"],
                )
                return self.send_json(
                    {"transaction": executed, "ok": verified},
                    200 if verified else 409,
                )

            if path == "/api/actions/power-plan":
                return self.send_json(
                    {
                        "ok": False,
                        "message": (
                            "Execucao direta desativada. "
                            "Use /api/transactions/prepare, /api/transactions/consent "
                            "e /api/transactions/execute."
                        ),
                    },
                    409,
                )

            return self.send_json({"error": "Acao bloqueada."}, 404)
        except Exception as exc:
            APP_LOG.write(
                "request_failed",
                level="ERROR",
                path=path,
                error_type=type(exc).__name__,
            )
            return self.send_json({"ok": False, "message": str(exc)}, 400)


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    DB.initialize()
    config = load_config(DB)
    _sync_observer_runner(config.observer_enabled)
    APP_LOG.write(
        "server_started",
        version=__version__,
        build=BUILD,
        selected_profile=config.selected_profile,
    )
    print(f"Zeus PC {__version__} ({BUILD})")
    print(f"http://{HOST}:{PORT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        OBSERVER_RUNNER.stop()
        APP_LOG.write("server_stopped")
        server.server_close()


if __name__ == "__main__":
    main()
