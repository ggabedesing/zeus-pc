"""Zeus local brain: model registry, runtime, router and invoke.

Public surface for increments 018+: everything needed to ask "which
model would serve this task?" and to run one turn against a loopback
llama-server. Machine mutations never happen here; they stay behind the
Transaction Engine.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .registry import (
    DEFAULT_MODEL_REGISTRY,
    MODEL_CATALOG_VERSION,
    BrainError,
    ModelRegistry,
    ModelSpec,
    ModelTask,
    build_default_model_registry,
)
from .router import CandidateOutcome, RoutingDecision, route_task
from .runtime import (
    DEFAULT_BASE_URL,
    RuntimeStatus,
    available_ram_gb,
    chat_complete,
    detect_llama_server,
    model_path,
    models_dir,
    probe_server,
    runtime_status,
)

__all__ = [
    "BrainError",
    "CandidateOutcome",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL_REGISTRY",
    "MODEL_CATALOG_VERSION",
    "ModelRegistry",
    "ModelSpec",
    "ModelTask",
    "RoutingDecision",
    "RuntimeStatus",
    "available_ram_gb",
    "build_default_model_registry",
    "chat_complete",
    "detect_llama_server",
    "invoke",
    "model_path",
    "models_dir",
    "probe_server",
    "route_task",
    "runtime_status",
]


def invoke(
    task: ModelTask | str,
    prompt: str,
    *,
    model_id: str | None = None,
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
    models_dir_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Route + complete one turn. Fail closed with BrainError reasons."""
    decision = route_task(
        task,
        registry=registry,
        models_dir=models_dir_path,
        available_ram_gb=available_ram_gb(),
        model_id=model_id,
    )
    if decision.model_id is None:
        raise BrainError(decision.reason)

    probe = probe_server(base_url)
    if not probe.get("reachable"):
        raise BrainError(
            "Runtime indisponivel: inicie o servidor do modelo com "
            "`python -m zeus_pc.brain.serve --model-id "
            f"{decision.model_id}`."
        )

    started = time.perf_counter()
    result = chat_complete(
        prompt,
        model=decision.model_id,
        base_url=base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "task": decision.task.value,
        "model_id": decision.model_id,
        "routing": decision.to_dict(),
        "content": result["content"],
        "usage": result["usage"],
        "elapsed_ms": elapsed_ms,
    }
