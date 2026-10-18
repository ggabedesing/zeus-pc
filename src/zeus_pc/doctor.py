"""`zeus doctor`: health report for the Zeus PC stack (increment 018).

Answers, in one deterministic JSON payload: is the database writable
and current? does `powercfg` exist (Windows)? where is llama-server?
which GGUF files are present and do they fit the available RAM?

Run it with:  python -m zeus_pc.doctor
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .brain import (
    DEFAULT_MODEL_REGISTRY,
    ModelRegistry,
    models_dir,
    runtime_status,
)
from .brain.manager import ModelManager
from .brain.runtime import model_path
from .persistence import ZeusDatabase


def build_doctor_report(
    database: ZeusDatabase | None = None,
    *,
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
    base_url: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    # 1. Persistence
    if database is not None:
        try:
            health = database.health()
            checks.append(
                {
                    "name": "persistence",
                    "ok": bool(health.writable and health.schema_version >= 1),
                    "detail": (
                        f"schema v{health.schema_version}, "
                        f"writable={health.writable}"
                    ),
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "persistence",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )

    # 2. Native Windows tooling
    powercfg = shutil.which("powercfg")
    checks.append(
        {
            "name": "powercfg",
            "ok": powercfg is not None,
            "detail": powercfg or "ausente (Windows obrigatorio para mutacoes)",
        }
    )

    # 3. Brain runtime
    kwargs = {"base_url": base_url} if base_url else {}
    runtime = runtime_status(**kwargs)
    checks.append(
        {
            "name": "brain_server",
            "ok": runtime.reachable,
            "detail": (
                "servidor de modelo respondendo"
                if runtime.reachable
                else (
                    f"llama-server em {runtime.server_path} (parado)"
                    if runtime.server_available
                    else "llama-server nao encontrado"
                )
            ),
        }
    )

    # 4. Model files and memory budget
    directory = models_dir()
    available = runtime.available_ram_gb
    model_states: list[dict[str, Any]] = []
    installed = 0
    for model_id in registry.ids():
        spec = registry.get(model_id)
        if spec is None:  # pragma: no cover - registry invariant
            continue
        present = model_path(spec.filename, directory).is_file()
        if present:
            installed += 1
        fits = (
            available is None or spec.min_ram_gb <= available
        )
        model_states.append(
            {
                "model_id": spec.model_id,
                "filename": spec.filename,
                "present": present,
                "size_gb": spec.size_gb,
                "min_ram_gb": spec.min_ram_gb,
                "fits_ram": fits,
            }
        )
    checks.append(
        {
            "name": "models_dir",
            "ok": directory.is_dir(),
            "detail": f"{directory} ({installed}/{len(model_states)} GGUF presentes)",
        }
    )

    # 5. Managed storage: tracked installs with recorded hashes
    storage: dict[str, Any] = {"tracked_count": 0, "installs": []}
    if database is not None:
        try:
            manager = ModelManager(database, registry=registry)
            storage = manager.storage_status()
            tracked = storage["tracked_count"]
            missing = sum(
                1 for item in storage["installs"] if not item["present"]
            )
            checks.append(
                {
                    "name": "model_storage",
                    "ok": missing == 0,
                    "detail": (
                        f"{tracked} rastreados, {storage['used_gb']} GB "
                        f"em {directory}"
                        + (
                            f" ({missing} rastreados sem arquivo)"
                            if missing
                            else ""
                        )
                    ),
                }
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "model_storage",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )

    # 6. Benchmarks: which models have measured scores
    scores: dict[str, float] = {}
    if database is not None:
        try:
            scores = database.benchmark_scores()
        except Exception:
            scores = {}
    checks.append(
        {
            "name": "brain_benchmarks",
            "ok": True,
            "detail": (
                f"{len(scores)} modelos com nota medida"
                if scores
                else "nenhum modelo medido ainda (POST /api/brain/benchmark)"
            ),
        }
    )

    return {
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
        "models": model_states,
        "runtime": runtime.to_dict(),
        "installed_models": installed,
        "registered_models": len(model_states),
        "storage": storage,
        "benchmark_scores": scores,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    database = ZeusDatabase(root / "data" / "zeus.db")
    database.initialize()
    report = build_doctor_report(database)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
