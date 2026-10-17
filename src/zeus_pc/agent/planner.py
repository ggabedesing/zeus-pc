"""Closed-schema agent planner for increment 015.

The planner turns an untrusted payload (for example what a local model
emits) into a validated AgentPlan. It never executes anything: every step
must be a registered semantic action, its arguments must pass the same
strict validator used by the Transaction Engine, and each step carries an
explicit risk level and consent requirement.

The plan is deterministic and side-effect free; execution stays in the
Transaction Engine (prepare -> consent -> execute).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..transaction import (
    ActionRegistry,
    DEFAULT_ACTION_REGISTRY,
    PreparedTransaction,
    TransactionEngine,
    TransactionError,
)

MAX_PLAN_STEPS = 4
_STEP_KEYS = {"action_id", "args", "rationale"}
_PAYLOAD_KEYS = {"steps", "explanation"}


class PlanError(RuntimeError):
    pass


class PlanRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _risk_for(action_id: str, registry: ActionRegistry) -> PlanRisk:
    definition = registry.get(action_id)
    if definition is None:
        return PlanRisk.HIGH
    if definition.required_privilege.upper() != "USER":
        return PlanRisk.HIGH
    if not definition.reversible:
        return PlanRisk.HIGH
    return PlanRisk.MEDIUM


@dataclass(frozen=True, slots=True)
class PlanStep:
    action_id: str
    args: dict[str, Any]
    rationale: str
    risk: PlanRisk
    consent_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "args": self.args,
            "rationale": self.rationale,
            "risk": self.risk.value,
            "consent_required": self.consent_required,
        }


@dataclass(frozen=True, slots=True)
class AgentPlan:
    plan_id: str
    created_at: str
    profile: str
    steps: tuple[PlanStep, ...]
    explanation: str
    max_risk: PlanRisk
    consent_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "profile": self.profile,
            "explanation": self.explanation,
            "max_risk": self.max_risk.value,
            "consent_required": self.consent_required,
            "steps": [step.to_dict() for step in self.steps],
        }


_RISK_ORDER = (
    PlanRisk.LOW,
    PlanRisk.MEDIUM,
    PlanRisk.HIGH,
)


def validate_plan(
    payload: Any,
    *,
    registry: ActionRegistry = DEFAULT_ACTION_REGISTRY,
    profile: str = "GENERAL",
) -> AgentPlan:
    """Validate an untrusted plan payload against the semantic registry."""
    if not isinstance(payload, dict):
        raise PlanError("Plano deve ser um objeto JSON.")
    unknown = set(payload) - _PAYLOAD_KEYS
    if unknown:
        raise PlanError(f"Campos desconhecidos no plano: {sorted(unknown)}.")

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("Plano precisa de ao menos um passo.")
    if len(raw_steps) > MAX_PLAN_STEPS:
        raise PlanError(f"Plano aceita no maximo {MAX_PLAN_STEPS} passos.")

    steps: list[PlanStep] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise PlanError(f"Passo {index + 1} deve ser um objeto.")
        unknown = set(raw) - _STEP_KEYS
        if unknown:
            raise PlanError(
                f"Campos desconhecidos no passo {index + 1}: {sorted(unknown)}."
            )

        action_id = str(raw.get("action_id") or "").strip().upper()
        if not action_id:
            raise PlanError(f"Passo {index + 1} sem action_id.")
        definition = registry.get(action_id)
        if definition is None:
            raise PlanError(
                f"Acao nao permitida pelo registro semantico: {action_id}."
            )

        args = raw.get("args")
        if not isinstance(args, dict):
            raise PlanError(
                f"Passo {index + 1}: args deve ser um objeto de argumentos."
            )
        try:
            normalized = definition.validate_args(args)
        except (TransactionError, ValueError, TypeError) as exc:
            raise PlanError(
                f"Passo {index + 1} ({action_id}): {exc}"
            )

        rationale = str(raw.get("rationale") or "").strip()
        risk = _risk_for(action_id, registry)
        steps.append(
            PlanStep(
                action_id=action_id,
                args=normalized,
                rationale=rationale,
                risk=risk,
                consent_required=True,
            )
        )

    max_risk = max(
        (step.risk for step in steps),
        key=lambda item: _RISK_ORDER.index(item),
    )
    return AgentPlan(
        plan_id=str(uuid.uuid4()),
        created_at=_iso(_utc_now()),
        profile=str(profile or "GENERAL").upper(),
        steps=tuple(steps),
        explanation=str(payload.get("explanation") or "").strip(),
        max_risk=max_risk,
        consent_required=any(step.consent_required for step in steps),
    )


def prepare_plan(
    engine: TransactionEngine,
    plan: AgentPlan,
) -> list[PreparedTransaction]:
    """Journal every plan step as a PREPARED transaction (no execution)."""
    prepared: list[PreparedTransaction] = []
    for step in plan.steps:
        prepared.append(engine.prepare(step.action_id, dict(step.args)))
    return prepared
