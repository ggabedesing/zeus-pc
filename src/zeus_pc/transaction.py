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

from .actions import GUID_RE, list_power_plans, set_power_plan
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
Apply = Callable[[dict[str, Any]], dict[str, Any]]
Rollback = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


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
    # Execucao real fica opcional: registracoes so preparam/consentem
    # enquanto apply/rollback nao forem declarados explicitamente.
    apply: Apply | None = None
    rollback: Rollback | None = None
    # JSON Schema declarativo dos argumentos, usado pelo catalogo de tools
    # (MCP/Obsidian/Codex). Nao substitui validate_args na execucao.
    arg_schema: dict[str, Any] | None = None


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
            "execution_enabled": False,
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


def _apply_power_plan(args: dict[str, Any]) -> dict[str, Any]:
    result = set_power_plan(str(args.get("guid") or "").lower())
    return {"ok": bool(result.ok), "message": result.message}


def _rollback_power_plan(
    before_state: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    previewed = str(
        ((plan or {}).get("args") or {}).get("guid") or ""
    ).lower()
    captured = str(before_state.get("active_guid") or "").lower()
    target = previewed if GUID_RE.fullmatch(previewed) else captured
    if not GUID_RE.fullmatch(target):
        return {"ok": False, "message": "Estado anterior sem GUID valido."}
    result = set_power_plan(target)
    return {"ok": bool(result.ok), "message": result.message}


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
            rollback=_rollback_power_plan,
            arg_schema={
                "type": "object",
                "properties": {
                    "guid": {
                        "type": "string",
                        "description": (
                            "GUID exato de um plano de energia existente "
                            "(powercfg /list)."
                        ),
                        "pattern": GUID_RE.pattern,
                    }
                },
                "required": ["guid"],
                "additionalProperties": False,
            },
        )
    )
    return registry


DEFAULT_ACTION_REGISTRY = build_default_registry()


class TransactionEngine:
    def __init__(
        self,
        database: ZeusDatabase,
        registry: ActionRegistry = DEFAULT_ACTION_REGISTRY,
        consent_ttl_seconds: int = 300,
    ) -> None:
        self.database = database
        self.registry = registry
        self.consent_ttl_seconds = min(
            max(int(consent_ttl_seconds), 30),
            1800,
        )
        self._resource_locks: dict[str, threading.Lock] = {}
        self._resource_locks_guard = threading.Lock()

    def executable_ids(self) -> list[str]:
        return sorted(
            item.action_id
            for item in (
                self.registry.get(action_id)
                for action_id in self.registry.ids()
            )
            if item is not None and item.apply is not None
        )

    def _resource_lock(self, resource_key: str) -> threading.Lock:
        with self._resource_locks_guard:
            lock = self._resource_locks.get(resource_key)
            if lock is None:
                lock = threading.Lock()
                self._resource_locks[resource_key] = lock
            return lock

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
        public["execution_enabled"] = public["state"] == TransactionState.CONSENTED.value
        public["message"] = (
            "Consentimento registrado. A acao pode ser executada agora."
            if public["execution_enabled"]
            else "Consentimento registrado. Execucao indisponivel para esta transacao."
        )
        return public

    @staticmethod
    def _safe_apply(
        apply: Apply,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            result = apply(args)
        except Exception as exc:
            return {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}",
            }
        if not isinstance(result, dict):
            return {"ok": False, "message": "APPLY retornou resultado invalido."}
        return result

    @staticmethod
    def _safe_verify(
        verify: Verifier,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            result = verify(args)
        except Exception as exc:
            return {
                "ok": False,
                "message": f"VERIFY indisponivel: {type(exc).__name__}: {exc}",
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "message": "VERIFY retornou resultado invalido.",
            }
        return result

    @staticmethod
    def _safe_rollback(
        rollback: Rollback,
        before_state: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            result = rollback(before_state, plan)
        except Exception as exc:
            return {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}",
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "message": "ROLLBACK retornou resultado invalido.",
            }
        return result

    def _execution_result(
        self,
        transaction_id: str,
        *,
        ok: bool,
        message: str,
    ) -> dict[str, Any]:
        transaction = self.database.get_transaction(transaction_id)
        if transaction is None:
            raise TransactionError("Transacao nao encontrada apos execucao.")
        return {
            "transaction": transaction,
            "state": transaction["state"],
            "ok": ok,
            "message": message,
        }

    def execute(self, transaction_id: str) -> dict[str, Any]:
        transaction = self.database.get_transaction(transaction_id)
        if transaction is None:
            raise TransactionError("Transacao nao encontrada.")
        if transaction["state"] != TransactionState.CONSENTED.value:
            raise TransactionError(
                "Execucao permitida apenas logo apos o consentimento unico."
            )

        definition = self.registry.get(str(transaction["action_id"] or ""))
        if definition is None:
            raise TransactionError("Acao nao permitida pelo registro semantico.")
        if definition.apply is None:
            raise TransactionError("Execucao nao habilitada para esta acao.")
        if definition.reversible and definition.rollback is None:
            raise TransactionError(
                "Acao reversivel sem estrategia de rollback nao pode ser executada."
            )

        try:
            normalized_args = definition.validate_args(transaction["args"])
        except TransactionError as exc:
            raise TransactionError(
                f"Argumentos nao sao mais validos para o estado atual: {exc}"
            )

        # Lock por recurso: apenas uma transacao executa por recurso por vez.
        with self._resource_lock(definition.resource_key):
            started = self.database.transition_transaction(
                transaction_id,
                from_state=TransactionState.CONSENTED.value,
                to_state=TransactionState.APPLYING.value,
                updated_at=_iso(_utc_now()),
            )
            if not started:
                raise TransactionError(
                    "Transacao nao esta mais apta para execucao."
                )

            apply_payload = self._safe_apply(definition.apply, normalized_args)
            if not apply_payload.get("ok"):
                message = str(
                    apply_payload.get("message") or "APPLY falhou."
                )
                self.database.transition_transaction(
                    transaction_id,
                    from_state=TransactionState.APPLYING.value,
                    to_state=TransactionState.FAILED.value,
                    updated_at=_iso(_utc_now()),
                    apply_result=apply_payload,
                    error_text=message,
                )
                return self._execution_result(
                    transaction_id,
                    ok=False,
                    message=message,
                )

            applied = self.database.transition_transaction(
                transaction_id,
                from_state=TransactionState.APPLYING.value,
                to_state=TransactionState.APPLIED.value,
                updated_at=_iso(_utc_now()),
                apply_result=apply_payload,
            )
            if not applied:
                raise TransactionError(
                    "Registro da transacao mudou durante o APPLY."
                )

            # VERIFY e independente do APPLY: relê o estado real do sistema.
            verify_payload = self._safe_verify(
                definition.verify,
                normalized_args,
            )
            if verify_payload.get("ok"):
                verified = self.database.transition_transaction(
                    transaction_id,
                    from_state=TransactionState.APPLIED.value,
                    to_state=TransactionState.VERIFIED.value,
                    updated_at=_iso(_utc_now()),
                    verify=verify_payload,
                )
                if not verified:
                    raise TransactionError(
                        "Registro da transacao mudou durante o VERIFY."
                    )
                return self._execution_result(
                    transaction_id,
                    ok=True,
                    message="Execucao aplicada e verificada com sucesso.",
                )

            # VERIFY falhou: rollback automatico para o BeforeState.
            rolling_back = self.database.transition_transaction(
                transaction_id,
                from_state=TransactionState.APPLIED.value,
                to_state=TransactionState.ROLLING_BACK.value,
                updated_at=_iso(_utc_now()),
                verify=verify_payload,
                error_text=(
                    "VERIFY nao confirmou o estado final; rollback iniciado."
                ),
            )
            if not rolling_back:
                raise TransactionError(
                    "Registro da transacao mudou antes do rollback."
                )

            assert definition.rollback is not None  # guarded above
            rollback_payload = self._safe_rollback(
                definition.rollback,
                dict(transaction["before_state"] or {}),
                dict(transaction.get("rollback") or {}),
            )
            if rollback_payload.get("ok"):
                self.database.transition_transaction(
                    transaction_id,
                    from_state=TransactionState.ROLLING_BACK.value,
                    to_state=TransactionState.ROLLED_BACK.value,
                    updated_at=_iso(_utc_now()),
                    rollback=rollback_payload,
                    error_text=(
                        "VERIFY falhou; rollback automatico concluido."
                    ),
                )
                return self._execution_result(
                    transaction_id,
                    ok=False,
                    message=(
                        "VERIFY falhou; o estado anterior foi restaurado."
                    ),
                )

            rollback_error = str(
                rollback_payload.get("message") or "ROLLBACK falhou."
            )
            self.database.transition_transaction(
                transaction_id,
                from_state=TransactionState.ROLLING_BACK.value,
                to_state=TransactionState.FAILED.value,
                updated_at=_iso(_utc_now()),
                rollback=rollback_payload,
                error_text=f"VERIFY e ROLLBACK falharam: {rollback_error}",
            )
            return self._execution_result(
                transaction_id,
                ok=False,
                message=(
                    "VERIFY falhou e o rollback nao pôde restaurar o estado "
                    f"anterior: {rollback_error}"
                ),
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
