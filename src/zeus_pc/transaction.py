from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable

from .actions import GUID_RE, apply_power_plan, list_power_plans
from .persistence import ZeusDatabase


class TransactionState(StrEnum):
    PREPARED = "PREPARED"
    CONSENTED = "CONSENTED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class TransactionError(RuntimeError):
    pass


Validator = Callable[[dict[str, Any]], dict[str, Any]]
BeforeCapture = Callable[[dict[str, Any]], dict[str, Any]]
Verifier = Callable[[dict[str, Any]], dict[str, Any]]
RollbackPreview = Callable[[dict[str, Any]], dict[str, Any] | None]
Applier = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_id: str
    resource_key: str
    required_privilege: str
    reversible: bool
    validate_args: Validator
    capture_before: BeforeCapture
    verify: Verifier
    rollback_preview: RollbackPreview
    apply: Applier | None = None

    @property
    def executable(self) -> bool:
        return self.apply is not None


class ResourceLockManager:
    """Process-local advisory locks keyed by resource so one resource mutates once at a time."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._owners: dict[str, str] = {}

    def acquire(self, resource_key: str, transaction_id: str) -> bool:
        with self._guard:
            if resource_key in self._owners:
                return False
            self._owners[resource_key] = transaction_id
            return True

    def release(self, resource_key: str, transaction_id: str) -> bool:
        with self._guard:
            if self._owners.get(resource_key) != transaction_id:
                return False
            del self._owners[resource_key]
            return True

    def owner(self, resource_key: str) -> str | None:
        with self._guard:
            return self._owners.get(resource_key)


class ActionRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ActionDefinition] = {}

    def register(self, definition: ActionDefinition) -> None:
        action_id = definition.action_id.strip().upper()
        if not action_id:
            raise ValueError("action_id is required")
        if action_id in self._items:
            raise ValueError(f"duplicate action_id: {action_id}")
        self._items[action_id] = definition

    def get(self, action_id: str) -> ActionDefinition | None:
        return self._items.get(str(action_id or "").strip().upper())

    def ids(self) -> list[str]:
        return sorted(self._items)

    def executable_ids(self) -> list[str]:
        return sorted(
            action_id
            for action_id, definition in self._items.items()
            if definition.executable
        )


@dataclass(slots=True)
class PreparedTransaction:
    transaction_id: str
    action_id: str
    state: TransactionState
    args: dict[str, Any]
    before_state: dict[str, Any]
    rollback_preview: dict[str, Any] | None
    created_at: str
    consent_expires_at: str
    consent_token: str
    execution_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "action_id": self.action_id,
            "state": self.state.value,
            "args": self.args,
            "before_state": self.before_state,
            "rollback_preview": self.rollback_preview,
            "created_at": self.created_at,
            "consent_expires_at": self.consent_expires_at,
            "consent_token": self.consent_token,
            "execution_enabled": self.execution_enabled,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_power_plan_args(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise TransactionError("Argumentos invalidos.")
    if set(args) != {"guid"}:
        raise TransactionError("SET_ACTIVE_POWER_PLAN aceita apenas o argumento guid.")

    guid = str(args.get("guid") or "").strip().lower()
    if not GUID_RE.fullmatch(guid):
        raise TransactionError("GUID invalido.")

    plans = list_power_plans()
    known = {str(item.get("guid") or "").lower() for item in plans}
    if guid not in known:
        raise TransactionError("Plano de energia nao encontrado.")

    return {"guid": guid}


def _capture_power_before(_: dict[str, Any]) -> dict[str, Any]:
    active = next(
        (item for item in list_power_plans() if item.get("active")),
        None,
    )
    if not active:
        raise TransactionError("Plano de energia ativo nao pôde ser confirmado.")
    return {
        "active_guid": str(active.get("guid") or "").lower(),
        "active_name": str(active.get("name") or ""),
    }


def _verify_power_target(args: dict[str, Any]) -> dict[str, Any]:
    expected = str(args.get("guid") or "").lower()
    active = next(
        (item for item in list_power_plans() if item.get("active")),
        None,
    )
    actual = str(active.get("guid") or "").lower() if active else None
    return {
        "ok": bool(actual and actual == expected),
        "expected_guid": expected,
        "actual_guid": actual,
        "source": "powercfg /list",
    }


def _apply_power_plan(args: dict[str, Any]) -> dict[str, Any]:
    guid = str(args.get("guid") or "").strip().lower()
    if not GUID_RE.fullmatch(guid):
        return {"ok": False, "message": "GUID invalido."}
    result = apply_power_plan(guid)
    return {
        "ok": result.ok,
        "message": result.message,
        "command": "powercfg /setactive",
    }


def _power_rollback_preview(
    before_state: dict[str, Any],
) -> dict[str, Any] | None:
    guid = str(before_state.get("active_guid") or "").lower()
    if not GUID_RE.fullmatch(guid):
        return None
    return {
        "action_id": "SET_ACTIVE_POWER_PLAN",
        "args": {"guid": guid},
    }


def build_default_registry() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            action_id="SET_ACTIVE_POWER_PLAN",
            resource_key="POWER_SCHEME",
            required_privilege="USER",
            reversible=True,
            validate_args=_validate_power_plan_args,
            capture_before=_capture_power_before,
            verify=_verify_power_target,
            rollback_preview=_power_rollback_preview,
            apply=_apply_power_plan,
        )
    )
    return registry


DEFAULT_ACTION_REGISTRY = build_default_registry()

_EXECUTION_REJECTIONS = {
    TransactionState.PREPARED.value: "Consentimento obrigatorio antes da execucao.",
    TransactionState.APPLYING.value: "Transacao em aplicacao. Reexecucao manual fica para a etapa de recuperacao.",
    TransactionState.APPLIED.value: "Transacao ja aplicada; aguarda VERIFY e nao pode reexecutar.",
    TransactionState.ROLLING_BACK.value: "Rollback ja esta em andamento.",
    TransactionState.FAILED.value: "Transacao falhou e nao pode ser reexecutada.",
    TransactionState.ROLLED_BACK.value: "Transacao revertida e nao pode ser reexecutada.",
    TransactionState.EXPIRED.value: "Consentimento expirado.",
    TransactionState.CANCELLED.value: "Transacao cancelada.",
}


class TransactionEngine:
    def __init__(
        self,
        database: ZeusDatabase,
        registry: ActionRegistry = DEFAULT_ACTION_REGISTRY,
        consent_ttl_seconds: int = 300,
        locks: ResourceLockManager | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.locks = locks if locks is not None else ResourceLockManager()
        self.consent_ttl_seconds = min(
            max(int(consent_ttl_seconds), 30),
            1800,
        )

    def prepare(
        self,
        action_id: str,
        args: dict[str, Any],
    ) -> PreparedTransaction:
        definition = self.registry.get(action_id)
        if definition is None:
            raise TransactionError("Acao nao permitida pelo registro semantico.")

        normalized_args = definition.validate_args(args)
        before_state = definition.capture_before(normalized_args)
        rollback_preview = (
            definition.rollback_preview(before_state)
            if definition.reversible
            else None
        )

        now = _utc_now()
        expires = now + timedelta(seconds=self.consent_ttl_seconds)
        transaction_id = str(uuid.uuid4())
        consent_token = secrets.token_urlsafe(32)
        consent_hash = _token_hash(consent_token)

        self.database.record_transaction(
            transaction_id=transaction_id,
            created_at=_iso(now),
            action_id=definition.action_id,
            state=TransactionState.PREPARED.value,
            args=normalized_args,
            before_state=before_state,
            rollback_preview=rollback_preview,
            consent_hash=consent_hash,
            consent_expires_at=_iso(expires),
        )

        return PreparedTransaction(
            transaction_id=transaction_id,
            action_id=definition.action_id,
            state=TransactionState.PREPARED,
            args=normalized_args,
            before_state=before_state,
            rollback_preview=rollback_preview,
            created_at=_iso(now),
            consent_expires_at=_iso(expires),
            consent_token=consent_token,
            execution_enabled=definition.executable,
        )

    def consume_consent(
        self,
        transaction_id: str,
        consent_token: str,
    ) -> dict[str, Any]:
        transaction = self.database.get_transaction(
            transaction_id,
            include_consent_hash=True,
        )
        if transaction is None:
            raise TransactionError("Transacao nao encontrada.")
        if transaction["state"] != TransactionState.PREPARED.value:
            raise TransactionError("Consentimento nao pode mais ser usado nesta transacao.")

        now = _utc_now()
        expires_at = _parse_iso(transaction["consent_expires_at"])
        if now >= expires_at:
            self.database.update_transaction(
                transaction_id,
                updated_at=_iso(now),
                state=TransactionState.EXPIRED.value,
                consent_hash=None,
            )
            raise TransactionError("Consentimento expirado.")

        supplied_hash = _token_hash(str(consent_token or ""))
        stored_hash = str(transaction.get("consent_hash") or "")
        if not stored_hash or not hmac.compare_digest(
            supplied_hash,
            stored_hash,
        ):
            raise TransactionError("Consentimento invalido.")

        consumed = self.database.consume_transaction_consent(
            transaction_id,
            expected_hash=stored_hash,
            updated_at=_iso(now),
        )
        if not consumed:
            raise TransactionError("Consentimento ja foi consumido ou a transacao mudou.")

        public = self.database.get_transaction(transaction_id)
        if public is None:
            raise TransactionError("Transacao nao encontrada apos consentimento.")
        definition = self.registry.get(public["action_id"])
        executable = bool(definition and definition.executable)
        public["execution_enabled"] = executable
        public["message"] = (
            "Consentimento registrado. Execute com POST /api/transactions/execute."
            if executable
            else "Consentimento registrado. Nenhum executor registrado para esta acao."
        )
        return public

    def _move(
        self,
        transaction_id: str,
        *,
        expected: TransactionState,
        target: TransactionState,
        **fields: Any,
    ) -> bool:
        return self.database.update_transaction(
            transaction_id,
            updated_at=_iso(_utc_now()),
            state=target.value,
            expected_state=expected.value,
            **fields,
        )

    def _run(
        self,
        stage: Callable[[dict[str, Any]], dict[str, Any]],
        args: dict[str, Any],
        failure_prefix: str,
    ) -> dict[str, Any]:
        try:
            evidence = stage(args)
        except Exception as exc:
            return {"ok": False, "message": f"{failure_prefix}: {exc}"}
        if not isinstance(evidence, dict):
            return {"ok": False, "message": f"{failure_prefix}: evidencia invalida."}
        return evidence

    def _result(
        self,
        transaction_id: str,
        fallback: dict[str, Any],
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        public = self.database.get_transaction(transaction_id) or fallback
        public["execution_enabled"] = True
        public["message"] = message
        public.update(extra)
        return public

    def execute(self, transaction_id: str) -> dict[str, Any]:
        transaction = self.database.get_transaction(transaction_id)
        if transaction is None:
            raise TransactionError("Transacao nao encontrada.")

        state = str(transaction["state"])
        if state == TransactionState.VERIFIED.value:
            return self._result(
                transaction_id,
                transaction,
                "Transacao ja verificada. Nenhuma alteracao aplicada novamente.",
                idempotent=True,
            )
        if state != TransactionState.CONSENTED.value:
            raise TransactionError(_EXECUTION_REJECTIONS.get(state, "Estado nao executavel."))

        definition = self.registry.get(str(transaction["action_id"]))
        if definition is None or not definition.executable:
            raise TransactionError("Nenhum executor registrado para esta acao.")

        args = dict(transaction["args"])
        before_state = dict(transaction["before_state"])
        current_before = definition.capture_before(args)
        if current_before != before_state:
            self.database.update_transaction(
                transaction_id,
                updated_at=_iso(_utc_now()),
                expected_state=TransactionState.CONSENTED.value,
                error_text="BeforeState desatualizado na execucao.",
            )
            raise TransactionError(
                "BeforeState desatualizado. Prepare uma nova transacao."
            )

        rollback_plan = (
            definition.rollback_preview(before_state)
            if definition.reversible
            else None
        )
        if definition.reversible and rollback_plan is None:
            raise TransactionError("Rollback indisponivel. Execucao bloqueada por seguranca.")

        resource_key = definition.resource_key
        if not self.locks.acquire(resource_key, transaction_id):
            owner = self.locks.owner(resource_key)
            raise TransactionError(
                f"Recurso {resource_key} ocupado pela transacao {owner}."
            )

        if not self._move(
            transaction_id,
            expected=TransactionState.CONSENTED,
            target=TransactionState.APPLYING,
        ):
            self.locks.release(resource_key, transaction_id)
            raise TransactionError("A transacao mudou de estado antes da execucao.")

        apply_evidence = self._run(
            definition.apply,
            args,
            "APPLY falhou",
        )
        if not apply_evidence.get("ok"):
            self._move(
                transaction_id,
                expected=TransactionState.APPLYING,
                target=TransactionState.FAILED,
                apply=apply_evidence,
                error_text=str(apply_evidence.get("message") or "APPLY falhou."),
            )
            self.locks.release(resource_key, transaction_id)
            return self._result(
                transaction_id,
                transaction,
                "APPLY falhou. Nenhuma alteracao confirmada pelo Windows.",
            )

        self._move(
            transaction_id,
            expected=TransactionState.APPLYING,
            target=TransactionState.APPLIED,
            apply=apply_evidence,
            error_text=None,
        )

        verify_evidence = self._run(definition.verify, args, "VERIFY falhou")
        if verify_evidence.get("ok"):
            self._move(
                transaction_id,
                expected=TransactionState.APPLIED,
                target=TransactionState.VERIFIED,
                verify=verify_evidence,
            )
            self.locks.release(resource_key, transaction_id)
            return self._result(
                transaction_id,
                transaction,
                "Acao aplicada e verificada por leitura independente.",
            )

        self._move(
            transaction_id,
            expected=TransactionState.APPLIED,
            target=TransactionState.ROLLING_BACK,
            verify=verify_evidence,
            error_text="VERIFY independente falhou. Rollback em andamento.",
        )

        rollback_args = dict(rollback_plan.get("args") or {})
        rollback_apply = self._run(definition.apply, rollback_args, "ROLLBACK falhou")
        rollback_verify = (
            self._run(definition.verify, rollback_args, "VERIFY do rollback falhou")
            if rollback_apply.get("ok")
            else None
        )
        rollback_evidence = {
            "target": rollback_plan,
            "apply": rollback_apply,
            "verify": rollback_verify,
        }

        if rollback_apply.get("ok") and (rollback_verify or {}).get("ok"):
            self._move(
                transaction_id,
                expected=TransactionState.ROLLING_BACK,
                target=TransactionState.ROLLED_BACK,
                rollback=rollback_evidence,
                error_text="VERIFY independente falhou. Estado anterior restaurado.",
            )
            self.locks.release(resource_key, transaction_id)
            return self._result(
                transaction_id,
                transaction,
                "VERIFY falhou. O estado anterior foi restaurado e confirmado.",
            )

        self._move(
            transaction_id,
            expected=TransactionState.ROLLING_BACK,
            target=TransactionState.FAILED,
            rollback=rollback_evidence,
            error_text="VERIFY falhou e o rollback nao foi confirmado.",
        )
        return self._result(
            transaction_id,
            transaction,
            "VERIFY falhou e o rollback nao foi confirmado. "
            "O recurso permanece travado ate recuperacao manual.",
            resource_locked=True,
        )

    def verify_target(
        self,
        action_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        definition = self.registry.get(action_id)
        if definition is None:
            raise TransactionError("Acao nao permitida pelo registro semantico.")
        normalized = definition.validate_args(args)
        return definition.verify(normalized)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.database.recent_transactions(limit)
