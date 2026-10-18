"""Walking skeleton runner (increment 021).

One command to prove the whole Zeus cycle on the real Windows PC:

    python -m zeus_pc.skeleton --model-id LLM_JP_1_8B ^
        --download-url https://huggingface.co/.../resolve/main/....gguf ^
        --accept-license

(PowerShell uses backticks or a single line; the flags are the same.)

Steps, in order: doctor -> model (download/import + SHA-256 verify)
-> serve (loopback llama-server as a subprocess) -> invoke (one
measured turn) -> transaction (switch power plan, then restore the
original with real consent tokens) -> JSON report.

Every step ends in "ok" | "falha" | "pulado" with the exact reason;
exit code 1 only when some step failed. A skipped prerequisite skips
its dependents instead of faking success. Machine mutations keep going
through the Transaction Engine — this runner never bypasses consent.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .actions import list_power_plans
from .brain import (
    DEFAULT_MODEL_REGISTRY,
    BrainError,
    ModelManager,
    ModelRegistry,
    invoke,
)
from .brain.runtime import DEFAULT_BASE_URL, detect_llama_server, probe_server
from .doctor import build_doctor_report
from .persistence import ZeusDatabase
from .transaction import TransactionEngine, TransactionError

DEFAULT_MODEL_ID = "LLM_JP_1_8B"
DEFAULT_PROMPT = "Responda em uma frase: o que voce faz neste PC?"
SERVE_PROBE_TIMEOUT = 2.0
STEP_OK = "ok"
STEP_FAIL = "falha"
STEP_SKIP = "pulado"


def _step(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **extra}


def _safe_step(
    steps: list[dict[str, Any]],
    name: str,
    runner: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """A diagnostic runner reports exceptions as failures, never crashes."""
    try:
        step = runner()
    except Exception as exc:
        step = _step(name, STEP_FAIL, f"{type(exc).__name__}: {exc}")
    steps.append(step)
    return step


def _log_tail(path: Path, limit: int = 400) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def step_doctor(database, registry: ModelRegistry) -> dict[str, Any]:
    try:
        report = build_doctor_report(database, registry=registry)
    except Exception as exc:
        return _step("doctor", STEP_FAIL, f"{type(exc).__name__}: {exc}")
    failing = [check["name"] for check in report["checks"] if not check["ok"]]
    if "persistence" in failing:
        return _step("doctor", STEP_FAIL, "banco de dados indisponivel.")
    detail = (
        "saude geral ok"
        if not failing
        else f"ok, com observacoes: {', '.join(failing)}"
    )
    return _step("doctor", STEP_OK, detail, failing_checks=failing)


def step_model(
    manager: ModelManager,
    model_id: str,
    *,
    download_url: str | None,
    accept_license: bool,
) -> dict[str, Any]:
    spec = manager.registry.get(model_id)
    if spec is None:
        return _step("model", STEP_FAIL, f"modelo nao registrado: {model_id!r}")

    target = manager.directory / spec.filename
    if not target.is_file():
        if not download_url:
            return _step(
                "model",
                STEP_SKIP,
                f"GGUF ausente: {spec.filename} — passe --download-url com "
                "--accept-license ou instale antes via POST /api/brain/install.",
            )
        if not accept_license:
            return _step(
                "model",
                STEP_FAIL,
                f"licenca nao aceita: {spec.license_name} — use --accept-license.",
            )
        try:
            manager.install(
                spec.model_id,
                accept_license=True,
                url=download_url,
            )
        except BrainError as exc:
            return _step("model", STEP_FAIL, f"instalacao falhou: {exc}")

    verification = manager.verify(spec.model_id)
    status = STEP_OK if verification["ok"] else STEP_FAIL
    return _step(
        "model",
        status,
        verification["detail"],
        sha256=verification.get("current_sha256"),
        model_id=spec.model_id,
    )


def step_serve(
    model_id: str,
    *,
    base_url: str,
    timeout: float,
    probe: Callable[..., dict[str, Any]],
    launcher: Callable[[list[str], Path], Any],
    detect_binary: Callable[[], str | None],
    sleeper: Callable[[float], None],
    log_path: Path,
) -> tuple[dict[str, Any], Any | None]:
    if detect_binary() is None:
        return (
            _step(
                "serve",
                STEP_SKIP,
                "llama-server nao encontrado (instale o llama.cpp ou defina "
                "ZEUS_LLAMA_SERVER).",
            ),
            None,
        )

    command = [
        sys.executable,
        "-m",
        "zeus_pc.brain.serve",
        "--model-id",
        model_id,
    ]
    try:
        process = launcher(command, log_path)
    except Exception as exc:
        return (
            _step("serve", STEP_FAIL, f"falha ao iniciar: {type(exc).__name__}: {exc}"),
            None,
        )

    attempts = max(1, int(timeout))
    for _ in range(attempts):
        result = probe(base_url, timeout=SERVE_PROBE_TIMEOUT)
        if result.get("reachable"):
            return (
                _step(
                    "serve",
                    STEP_OK,
                    f"servidor de {model_id} respondendo em {base_url}.",
                ),
                process,
            )
        exit_code = process.poll()
        if exit_code is not None:
            return (
                _step(
                    "serve",
                    STEP_FAIL,
                    f"servidor encerrou cedo (codigo {exit_code}); "
                    f"log: {_log_tail(log_path) or 'sem saida'}",
                ),
                process,
            )
        sleeper(1.0)

    return (
        _step(
            "serve",
            STEP_FAIL,
            f"servidor nao respondeu em {timeout:.0f}s; "
            f"log: {_log_tail(log_path) or 'sem saida'}",
        ),
        process,
    )


def step_invoke(
    model_id: str,
    prompt: str,
    *,
    base_url: str,
    invoker: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    try:
        result = invoker(
            "CHAT",
            prompt,
            model_id=model_id,
            base_url=base_url,
        )
    except BrainError as exc:
        return _step("invoke", STEP_FAIL, str(exc))
    except Exception as exc:
        return _step("invoke", STEP_FAIL, f"{type(exc).__name__}: {exc}")
    content = str(result.get("content") or "")
    return _step(
        "invoke",
        STEP_OK,
        f"{content[:120]}",
        model_id=result.get("model_id"),
        elapsed_ms=result.get("elapsed_ms"),
        usage=result.get("usage"),
    )


def step_transaction(
    engine: TransactionEngine,
    plans: list[dict[str, Any]],
) -> dict[str, Any]:
    active = next(
        (item for item in plans if item.get("active")), None
    )
    alternatives = [item for item in plans if not item.get("active")]
    if active is None or not alternatives:
        return _step(
            "transaction",
            STEP_SKIP,
            "e preciso um plano ativo e outro inativo para alternar "
            "(Windows com powercfg obrigatorio).",
        )
    target = alternatives[0]

    def run(guid: str) -> dict[str, Any]:
        prepared = engine.prepare(
            "SET_ACTIVE_POWER_PLAN", {"guid": str(guid)}
        )
        engine.consume_consent(
            prepared.transaction_id, prepared.consent_token
        )
        return engine.execute(prepared.transaction_id)

    try:
        switched = run(str(target["guid"]))
    except TransactionError as exc:
        return _step("transaction", STEP_FAIL, f"preparacao da troca falhou: {exc}")

    if not switched.get("ok"):
        return _step(
            "transaction",
            STEP_FAIL,
            f"troca falhou (estado {switched.get('state')}): "
            f"{switched.get('message')}",
            transaction_id=switched.get("transaction", {}).get("transaction_id"),
        )

    try:
        restored = run(str(active["guid"]))
    except TransactionError as exc:
        return _step(
            "transaction",
            STEP_FAIL,
            f"FAILA CRITICA: troca ok mas a restauracao nem preparou "
            f"({exc}); plano original era {active.get('name')}",
        )
    if not restored.get("ok"):
        return _step(
            "transaction",
            STEP_FAIL,
            f"FAILA CRITICA: plano original {active.get('name')} NAO "
            f"restaurado (estado {restored.get('state')}): "
            f"{restored.get('message')}",
        )

    return _step(
        "transaction",
        STEP_OK,
        f"alternado para {target.get('name')} e restaurado para "
        f"{active.get('name')}: 2 transacoes com consentimento real.",
        switch_state=switched.get("state"),
        restore_state=restored.get("state"),
    )


def _default_launcher(command: list[str], log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags if os.name == "nt" else 0,
    )
    # guarda o handle no processo para fechar depois do terminate
    process._zeus_log_handle = handle  # type: ignore[attr-defined]
    return process


def _stop_process(process: Any) -> None:
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    handle = getattr(process, "_zeus_log_handle", None)
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass


def run_skeleton(
    database,
    *,
    model_registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
    model_id: str = DEFAULT_MODEL_ID,
    download_url: str | None = None,
    accept_license: bool = False,
    prompt: str = DEFAULT_PROMPT,
    base_url: str = DEFAULT_BASE_URL,
    serve_timeout: float = 90.0,
    probe: Callable[..., dict[str, Any]] = probe_server,
    launcher: Callable[[list[str], Path], Any] | None = None,
    invoker: Callable[..., dict[str, Any]] | None = None,
    plan_source: Callable[[], list[dict[str, Any]]] = list_power_plans,
    detect_binary: Callable[[], str | None] = detect_llama_server,
    engine: TransactionEngine | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Run every step in order; dependents skip when a prerequisite did."""
    steps: list[dict[str, Any]] = []
    transaction_engine = engine or TransactionEngine(database)
    manager = ModelManager(database, registry=model_registry)
    launcher_fn = launcher or _default_launcher
    invoker_fn = invoker or invoke
    serve_log = log_path or (
        Path(tempfile.gettempdir()) / f"zeus-serve-{model_id}.log"
    )

    process: Any = None
    try:
        doctor = _safe_step(
            steps,
            "doctor",
            lambda: step_doctor(database, model_registry),
        )

        model = _safe_step(
            steps,
            "model",
            lambda: step_model(
                manager,
                model_id,
                download_url=download_url,
                accept_license=accept_license,
            ),
        )

        if doctor["status"] == STEP_FAIL or model["status"] != STEP_OK:
            reason = (
                "etapa 'doctor' reprovada"
                if doctor["status"] == STEP_FAIL
                else f"etapa 'model' em {model['status']}"
            )
            serve = _step(
                "serve", STEP_SKIP, f"{reason}; nada a subir."
            )
            invoke_step = _step(
                "invoke", STEP_SKIP, "etapa 'serve' nao concluida."
            )
        else:
            serve, process = step_serve(
                model_id,
                base_url=base_url,
                timeout=serve_timeout,
                probe=probe,
                launcher=launcher_fn,
                detect_binary=detect_binary,
                sleeper=sleeper,
                log_path=serve_log,
            )
            if serve["status"] == STEP_OK:
                invoke_step = step_invoke(
                    model_id,
                    prompt,
                    base_url=base_url,
                    invoker=invoker_fn,
                )
            else:
                invoke_step = _step(
                    "invoke", STEP_SKIP, "etapa 'serve' nao concluida."
                )
        steps.append(serve)
        steps.append(invoke_step)

        _safe_step(
            steps,
            "transaction",
            lambda: step_transaction(transaction_engine, plan_source()),
        )
    finally:
        _stop_process(process)

    return {
        "ok": all(step["status"] != STEP_FAIL for step in steps),
        "model_id": model_id,
        "steps": steps,
        "counts": {
            status: sum(1 for step in steps if step["status"] == status)
            for status in (STEP_OK, STEP_FAIL, STEP_SKIP)
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zeus_pc.skeleton",
        description=(
            "Walking skeleton: modelo -> serve -> invoke medido -> "
            "transacao real com restauracao."
        ),
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--download-url", default=None)
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--serve-timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    database = ZeusDatabase(root / "data" / "zeus.db")
    database.initialize()

    report = run_skeleton(
        database,
        model_id=args.model_id,
        download_url=args.download_url,
        accept_license=args.accept_license,
        prompt=args.prompt,
        base_url=args.base_url,
        serve_timeout=args.serve_timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
