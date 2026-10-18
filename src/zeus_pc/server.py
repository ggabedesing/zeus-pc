from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import BUILD, __version__
from .actions import list_power_plans
from .agent import build_context, build_tool_catalog, validate_plan
from .app_logging import StructuredLog
from .brain import (
    DEFAULT_MODEL_REGISTRY,
    BrainError,
    ModelManager,
    invoke,
    run_benchmark,
)
from .config import CONFIG_SCHEMA_VERSION, load_config, save_config
from .doctor import build_doctor_report
from .engine import build_recommendations
from .observer import OBSERVER, OBSERVER_RUNNER
from .optimization import build_optimization_plan, snapshot_from_payload
from .persistence import ZeusDatabase
from .process_observer import PROCESS_OBSERVER
from .probes import collect_all
from .transaction import TransactionEngine

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


# --- Guards da API local (increment 020) ----------------------------------
# DNS rebinding: um site malicioso resolve para 127.0.0.1 e envia Host
# proprio -> negado. CSRF via navegador: Origin de outra origem ou
# Content-Type "simples" (text/plain via no-cors) -> negado. Clientes
# nao-navegadores (curl sem Origin, scripts) continuam funcionando.
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
ALLOWED_POST_CONTENT_TYPES = frozenset({"application/json"})


def _split_host(value: str) -> str:
    """Hostname sem porta, preservando o formato colchete do IPv6."""
    cleaned = str(value or "").strip().lower()
    if cleaned.startswith("["):
        end = cleaned.find("]")
        return cleaned[: end + 1] if end != -1 else cleaned
    if cleaned.count(":") == 1:
        return cleaned.rsplit(":", 1)[0]
    return cleaned


def host_is_loopback(host_header: str | None) -> bool:
    return _split_host(host_header or "") in LOOPBACK_HOSTNAMES


def origin_is_loopback(origin: str | None, port: int) -> bool:
    """Same-origin only: hostname loopback E porta do servidor."""
    cleaned = str(origin or "").strip().rstrip("/")
    if not cleaned:
        return True  # clientes sem navegador nao enviam Origin
    if "://" not in cleaned:
        return False
    scheme, rest = cleaned.split("://", 1)
    if scheme != "http":
        return False
    host_part = rest.split("/", 1)[0]
    if host_part.startswith("["):
        end = host_part.find("]")
        hostname = host_part[: end + 1]
        has_port = host_part[end + 1 :].startswith(":")
        origin_port = host_part[end + 2 :] if has_port else ""
    elif host_part.count(":") == 1:
        hostname, origin_port = host_part.rsplit(":", 1)
    else:
        hostname, origin_port = host_part, ""
    if hostname not in LOOPBACK_HOSTNAMES:
        return False
    if origin_port and origin_port != str(port):
        return False
    return True


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

    def reject(self, message: str, status: int = 403, reason: str = "guard"):
        APP_LOG.write(
            "request_rejected",
            level="ERROR",
            path=self.path,
            reason=reason,
            status=status,
        )
        # Fecha a conexao: o corpo da requisicao pode nao ter sido lido.
        self.close_connection = True
        return self.send_json({"ok": False, "message": message}, status)

    def guard_host(self) -> bool:
        if host_is_loopback(self.headers.get("Host")):
            return True
        self.reject(
            f"Host negado: {self.headers.get('Host')!r} (somente loopback).",
            reason="host",
        )
        return False

    def guard_mutation(self) -> bool:
        if not self.guard_host():
            return False
        port = int(self.server.server_address[1])
        origin = self.headers.get("Origin")
        if origin and not origin_is_loopback(origin, port):
            self.reject(f"Origem negada: {origin!r}.", reason="origin")
            return False
        content_type = (
            self.headers.get("Content-Length")
        ) and (
            self.headers.get("Content-Type") or ""
        ).split(";", 1)[0].strip().lower()
        if content_type and content_type not in ALLOWED_POST_CONTENT_TYPES:
            self.reject(
                "Content-Type deve ser application/json.",
                status=415,
                reason="content_type",
            )
            return False
        return True

    def do_GET(self):
        if not self.guard_host():
            return
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
                "enabled_actions": TRANSACTIONS.executable_ids(),
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
                "executable_actions": TRANSACTIONS.executable_ids(),
            })
        if path == "/api/agent/context":
            history = DB.recent_scans(1)
            if not history:
                return self.send_json(
                    {"error": "Faca uma analise primeiro para montar o contexto."},
                    409,
                )
            latest = history[0]
            profile = query.get("profile", [latest["profile"]])[0]
            session_id = query.get("session_id", [None])[0]
            snapshot = snapshot_from_payload(latest["snapshot"])
            optimization = build_optimization_plan(
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
            baseline = OBSERVER.baseline(60, session_id=session_id)
            context = build_context(
                profile=profile,
                snapshot=latest["snapshot"],
                optimization_plan=optimization.to_dict(),
                baseline=baseline,
                source_scan_id=latest["id"],
            )
            return self.send_json({"context": context.to_dict()})
        if path == "/api/agent/tools":
            return self.send_json(
                build_tool_catalog(TRANSACTIONS.registry).to_dict()
            )
        if path == "/api/brain/models":
            return self.send_json(DEFAULT_MODEL_REGISTRY.to_dict())
        if path == "/api/brain/status":
            return self.send_json(build_doctor_report(DB))
        if path == "/api/brain/storage":
            budget = query.get("budget_gb", [None])[0]
            try:
                manager = ModelManager(DB)
                return self.send_json(
                    manager.storage_status(
                        budget_gb=float(budget) if budget else None
                    )
                )
            except BrainError as exc:
                return self.send_json({"ok": False, "message": str(exc)}, 409)
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
            not_found = WEB / "404.html"
            if not_found.is_file():
                data = not_found.read_bytes()
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return
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
        if not self.guard_mutation():
            return
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

            if path == "/api/agent/plan":
                config = load_config(DB)
                agent_plan = validate_plan(
                    payload,
                    registry=TRANSACTIONS.registry,
                    profile=config.selected_profile,
                )
                APP_LOG.write(
                    "agent_plan_validated",
                    plan_id=agent_plan.plan_id,
                    step_count=len(agent_plan.steps),
                    max_risk=agent_plan.max_risk.value,
                )
                return self.send_json({"plan": agent_plan.to_dict()})

            if path == "/api/transactions/execute":
                transaction_id = str(payload.get("transaction_id") or "")
                result = TRANSACTIONS.execute(transaction_id)
                transaction = result["transaction"]
                if result["ok"]:
                    level = "INFO"
                    event = "transaction_verified"
                elif transaction["state"] == "ROLLED_BACK":
                    level = "ERROR"
                    event = "transaction_rolled_back"
                else:
                    level = "ERROR"
                    event = "transaction_failed"
                APP_LOG.write(
                    event,
                    level=level,
                    transaction_id=transaction_id,
                    action_id=transaction["action_id"],
                    state=transaction["state"],
                    message=result["message"],
                )
                return self.send_json(result)

            if path == "/api/brain/invoke":
                options: dict = {}
                if payload.get("max_tokens") is not None:
                    options["max_tokens"] = int(payload.get("max_tokens"))
                if payload.get("temperature") is not None:
                    options["temperature"] = float(payload.get("temperature"))
                model_id = payload.get("model_id")
                scores = DB.benchmark_scores()
                if scores:
                    options["scores"] = scores
                result = invoke(
                    str(payload.get("task") or ""),
                    str(payload.get("prompt") or ""),
                    model_id=str(model_id) if model_id else None,
                    **options,
                )
                DB.touch_model_used(result["model_id"])
                APP_LOG.write(
                    "brain_invoked",
                    task=result["task"],
                    model_id=result["model_id"],
                    elapsed_ms=result["elapsed_ms"],
                )
                return self.send_json({"result": result})

            if path == "/api/brain/install":
                manager = ModelManager(DB)
                install = manager.install(
                    str(payload.get("model_id") or ""),
                    accept_license=bool(payload.get("accept_license")),
                    url=(
                        str(payload["url"]) if payload.get("url") else None
                    ),
                    source_path=(
                        str(payload["source_path"])
                        if payload.get("source_path")
                        else None
                    ),
                    expected_sha256=(
                        str(payload["expected_sha256"])
                        if payload.get("expected_sha256")
                        else None
                    ),
                )
                APP_LOG.write(
                    "brain_model_installed",
                    model_id=install["model_id"],
                    source=install["source"],
                    size_bytes=install["size_bytes"],
                )
                return self.send_json({"install": install}, 201)

            if path == "/api/brain/verify":
                manager = ModelManager(DB)
                verification = manager.verify(
                    str(payload.get("model_id") or "")
                )
                APP_LOG.write(
                    (
                        "brain_model_verified"
                        if verification["ok"]
                        else "brain_model_verify_failed"
                    ),
                    level="INFO" if verification["ok"] else "ERROR",
                    model_id=verification["model_id"],
                    status=verification["status"],
                )
                return self.send_json({"verification": verification})

            if path == "/api/brain/benchmark":
                languages = payload.get("languages")
                kwargs: dict = {}
                if isinstance(languages, list) and languages:
                    kwargs["languages"] = tuple(
                        str(item) for item in languages
                    )
                benchmark = run_benchmark(
                    str(payload.get("model_id") or ""),
                    database=DB,
                    **kwargs,
                )
                APP_LOG.write(
                    "brain_benchmark_completed",
                    model_id=benchmark["model_id"],
                    score=benchmark["score"],
                    prompt_count=benchmark["prompt_count"],
                )
                return self.send_json({"benchmark": benchmark})

            if path == "/api/brain/prune":
                manager = ModelManager(DB)
                budget = payload.get("budget_gb")
                result = manager.prune(
                    budget_gb=float(budget) if budget is not None else None,
                    confirm=bool(payload.get("confirm")),
                )
                APP_LOG.write(
                    "brain_models_pruned",
                    removed=len(result["removed"]),
                    freed_gb=result["freed_gb"],
                )
                return self.send_json({"prune": result})

            if path == "/api/actions/power-plan":
                return self.send_json(
                    {
                        "ok": False,
                        "message": (
                            "Execucao direta desativada. "
                            "Acoes reais exigirao o Transaction Engine completo."
                        ),
                    },
                    409,
                )

            return self.send_json({"error": "Acao bloqueada."}, 404)
        except BrainError as exc:
            APP_LOG.write(
                "brain_rejected",
                level="ERROR",
                path=path,
                error=str(exc),
            )
            return self.send_json({"ok": False, "message": str(exc)}, 409)
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
