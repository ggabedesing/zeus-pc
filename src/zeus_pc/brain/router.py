"""Deterministic model router for the Zeus brain (increment 018).

Given a task, it picks the best available model from the registry:
strength match first, then GGUF file present, then RAM budget, then
quality rank. No clock, no randomness: the same filesystem and the same
RAM always produce the same decision. When nothing qualifies, the
decision explains exactly why each candidate was skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import (
    DEFAULT_MODEL_REGISTRY,
    BrainError,
    ModelRegistry,
    ModelSpec,
    ModelTask,
)


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    model_id: str
    status: str  # "ok" | "sem_tarefa" | "sem_arquivo" | "memoria"
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    task: ModelTask
    model_id: str | None
    reason: str
    considered: tuple[CandidateOutcome, ...]

    @property
    def ok(self) -> bool:
        return self.model_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.value,
            "model_id": self.model_id,
            "ok": self.ok,
            "reason": self.reason,
            "considered": [item.to_dict() for item in self.considered],
        }


def _file_present(spec: ModelSpec, directory: Path) -> bool:
    return (directory / spec.filename).is_file()


def route_task(
    task: ModelTask | str,
    *,
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
    models_dir: Path | None = None,
    available_ram_gb: float | None = None,
    model_id: str | None = None,
) -> RoutingDecision:
    """Route a task to one model, or explain why none is usable."""
    try:
        wanted = ModelTask(str(task or "").strip().upper())
    except ValueError as exc:
        valid = [item.value for item in ModelTask]
        raise BrainError(f"tarefa desconhecida: {task!r} (validas: {valid})") from exc

    directory = Path(models_dir) if models_dir is not None else None
    from .runtime import models_dir as default_models_dir

    directory = directory if directory is not None else default_models_dir()

    considered: list[CandidateOutcome] = []

    # Explicit choice: strict, fail closed on any problem.
    if model_id:
        spec = registry.get(model_id)
        if spec is None:
            raise BrainError(f"modelo nao registrado: {model_id!r}")
        if not _file_present(spec, directory):
            raise BrainError(
                f"GGUF ausente para {spec.model_id}: {spec.filename}"
            )
        if (
            available_ram_gb is not None
            and spec.min_ram_gb > available_ram_gb
        ):
            raise BrainError(
                f"{spec.model_id} exige {spec.min_ram_gb} GB e ha "
                f"{available_ram_gb} GB disponiveis."
            )
        return RoutingDecision(
            task=wanted,
            model_id=spec.model_id,
            reason="escolha explicita do usuario.",
            considered=(
                CandidateOutcome(spec.model_id, "ok", "selecionado manualmente."),
            ),
        )

    # Automatic: every registered model is reported, in id order.
    chosen: ModelSpec | None = None
    for model_id_sorted in registry.ids():
        spec = registry.get(model_id_sorted)
        if spec is None:  # pragma: no cover - registry invariant
            continue
        if wanted not in spec.strengths:
            considered.append(
                CandidateOutcome(
                    spec.model_id,
                    "sem_tarefa",
                    f"nao serve {wanted.value}.",
                )
            )
            continue
        if not _file_present(spec, directory):
            considered.append(
                CandidateOutcome(
                    spec.model_id,
                    "sem_arquivo",
                    f"GGUF ausente: {spec.filename}",
                )
            )
            continue
        if (
            available_ram_gb is not None
            and spec.min_ram_gb > available_ram_gb
        ):
            considered.append(
                CandidateOutcome(
                    spec.model_id,
                    "memoria",
                    f"exige {spec.min_ram_gb} GB; ha {available_ram_gb} GB.",
                )
            )
            continue
        considered.append(
            CandidateOutcome(spec.model_id, "ok", "disponivel.")
        )
        if chosen is None:
            chosen = spec

    if chosen is None:
        return RoutingDecision(
            task=wanted,
            model_id=None,
            reason=(
                f"nenhum modelo disponivel para {wanted.value}; "
                "baixe um GGUF compativel e confira a memoria."
            ),
            considered=tuple(considered),
        )

    return RoutingDecision(
        task=wanted,
        model_id=chosen.model_id,
        reason=(
            f"melhor rank {chosen.quality_rank} entre os disponiveis "
            f"para {wanted.value}."
        ),
        considered=tuple(considered),
    )
