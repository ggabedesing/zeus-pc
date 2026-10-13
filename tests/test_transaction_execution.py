import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.actions import apply_power_plan
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    TransactionEngine,
    TransactionError,
    TransactionState,
    _apply_power_plan,
)


DEFAULT = object()
VALID_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"


def validate_args(args):
    if not isinstance(args, dict) or set(args) != {"value"}:
        raise TransactionError("invalid args")
    return {"value": int(args["value"])}


def rollback_preview(before_state):
    return {
        "action_id": "TEST_ACTION",
        "args": {"value": before_state["value"]},
    }


class Stages:
    def __init__(self):
        self.apply_calls = []
        self.verify_calls = []
        self.before_state = {"value": 1}

    def capture_before(self, args):
        return dict(self.before_state)

    def apply(self, args):
        self.apply_calls.append(dict(args))
        return {"ok": True, "message": "aplicado"}

    def verify(self, args):
        self.verify_calls.append(dict(args))
        return {"ok": True, "actual": args["value"]}


class TransactionExecutionTests(unittest.TestCase):
    def make_registry(self, stages, apply=DEFAULT, verify=DEFAULT):
        registry = ActionRegistry()
        registry.register(
            ActionDefinition(
                action_id="TEST_ACTION",
                resource_key="TEST_RESOURCE",
                required_privilege="USER",
                reversible=True,
                validate_args=validate_args,
                capture_before=stages.capture_before,
                verify=stages.verify if verify is DEFAULT else verify,
                rollback_preview=rollback_preview,
                apply=stages.apply if apply is DEFAULT else apply,
            )
        )
        return registry

    def make_engine(self, db, registry):
        return TransactionEngine(
            db,
            registry=registry,
            consent_ttl_seconds=60,
        )

    def consent(self, engine):
        prepared = engine.prepare("TEST_ACTION", {"value": 2})
        engine.consume_consent(
            prepared.transaction_id,
            prepared.consent_token,
        )
        return prepared

    def test_happy_path_verifies_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            prepared = self.consent(engine)

            result = engine.execute(prepared.transaction_id)
            stored = db.get_transaction(prepared.transaction_id)

            self.assertEqual(result["state"], "VERIFIED")
            self.assertTrue(result["execution_enabled"])
            self.assertEqual(
                result["message"],
                "Acao aplicada e verificada por leitura independente.",
            )
            self.assertEqual(stored["state"], "VERIFIED")
            self.assertEqual(stages.apply_calls, [{"value": 2}])
            self.assertEqual(stages.verify_calls, [{"value": 2}])
            self.assertTrue(stored["apply"]["ok"])
            self.assertTrue(stored["verify"]["ok"])
            self.assertIsNone(stored["error_text"])
            self.assertIsNone(engine.locks.owner("TEST_RESOURCE"))

    def test_apply_failure_moves_to_failed_without_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()

            def apply_fails(args):
                stages.apply_calls.append(dict(args))
                return {"ok": False, "message": "powercfg recusou"}

            engine = self.make_engine(
                db,
                self.make_registry(stages, apply=apply_fails),
            )
            prepared = self.consent(engine)

            result = engine.execute(prepared.transaction_id)
            stored = db.get_transaction(prepared.transaction_id)

            self.assertEqual(result["state"], "FAILED")
            self.assertTrue(result["execution_enabled"])
            self.assertEqual(
                result["message"],
                "APPLY falhou. Nenhuma alteracao confirmada pelo Windows.",
            )
            self.assertEqual(stored["state"], "FAILED")
            self.assertEqual(stored["error_text"], "powercfg recusou")
            self.assertFalse(stored["apply"]["ok"])
            self.assertIsNone(stored["verify"])
            self.assertEqual(stages.verify_calls, [])
            self.assertIsNone(engine.locks.owner("TEST_RESOURCE"))

    def test_verify_failure_rolls_back_and_confirms_restoration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()

            def verify_by_args(args):
                stages.verify_calls.append(dict(args))
                return {"ok": args["value"] == 1, "actual": args["value"]}

            engine = self.make_engine(
                db,
                self.make_registry(stages, verify=verify_by_args),
            )
            prepared = self.consent(engine)

            result = engine.execute(prepared.transaction_id)
            stored = db.get_transaction(prepared.transaction_id)

            self.assertEqual(result["state"], "ROLLED_BACK")
            self.assertEqual(
                result["message"],
                "VERIFY falhou. O estado anterior foi restaurado e confirmado.",
            )
            self.assertEqual(stored["state"], "ROLLED_BACK")
            self.assertEqual(stages.apply_calls, [{"value": 2}, {"value": 1}])
            self.assertEqual(stages.verify_calls, [{"value": 2}, {"value": 1}])
            rollback = stored["rollback"]
            self.assertEqual(rollback["target"]["args"], {"value": 1})
            self.assertTrue(rollback["apply"]["ok"])
            self.assertTrue(rollback["verify"]["ok"])
            self.assertEqual(
                stored["error_text"],
                "VERIFY independente falhou. Estado anterior restaurado.",
            )
            self.assertIsNone(engine.locks.owner("TEST_RESOURCE"))

    def test_rollback_failure_marks_failed_and_keeps_resource_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()

            def apply_target_only(args):
                stages.apply_calls.append(dict(args))
                return {
                    "ok": args["value"] == 2,
                    "message": "restauracao indisponivel",
                }

            def verify_fails(args):
                stages.verify_calls.append(dict(args))
                return {"ok": False, "actual": None}

            engine = self.make_engine(
                db,
                self.make_registry(
                    stages,
                    apply=apply_target_only,
                    verify=verify_fails,
                ),
            )
            prepared = self.consent(engine)

            result = engine.execute(prepared.transaction_id)
            stored = db.get_transaction(prepared.transaction_id)

            self.assertEqual(result["state"], "FAILED")
            self.assertTrue(result["resource_locked"])
            self.assertIn(
                "O recurso permanece travado ate recuperacao manual.",
                result["message"],
            )
            self.assertEqual(stored["state"], "FAILED")
            self.assertEqual(
                stored["error_text"],
                "VERIFY falhou e o rollback nao foi confirmado.",
            )
            rollback = stored["rollback"]
            self.assertEqual(rollback["target"]["args"], {"value": 1})
            self.assertFalse(rollback["apply"]["ok"])
            self.assertIsNone(rollback["verify"])
            self.assertEqual(stages.verify_calls, [{"value": 2}])
            self.assertEqual(
                engine.locks.owner("TEST_RESOURCE"),
                prepared.transaction_id,
            )

    def test_execute_without_consent_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            prepared = engine.prepare("TEST_ACTION", {"value": 2})

            with self.assertRaises(TransactionError) as ctx:
                engine.execute(prepared.transaction_id)

            self.assertIn("Consentimento obrigatorio", str(ctx.exception))
            self.assertEqual(stages.apply_calls, [])
            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "PREPARED")

    def test_verified_replay_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            prepared = self.consent(engine)

            first = engine.execute(prepared.transaction_id)
            second = engine.execute(prepared.transaction_id)

            self.assertEqual(first["state"], "VERIFIED")
            self.assertNotIn("idempotent", first)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["state"], "VERIFIED")
            self.assertEqual(
                second["message"],
                "Transacao ja verificada. Nenhuma alteracao aplicada novamente.",
            )
            self.assertEqual(len(stages.apply_calls), 1)
            self.assertEqual(len(stages.verify_calls), 1)
            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "VERIFIED")
            self.assertIsNone(engine.locks.owner("TEST_RESOURCE"))

    def test_terminal_and_in_flight_states_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            prepared = self.consent(engine)

            for state in [
                TransactionState.FAILED,
                TransactionState.ROLLED_BACK,
                TransactionState.EXPIRED,
                TransactionState.CANCELLED,
                TransactionState.APPLYING,
                TransactionState.APPLIED,
                TransactionState.ROLLING_BACK,
            ]:
                db.update_transaction(
                    prepared.transaction_id,
                    updated_at="2026-01-01T00:00:00Z",
                    state=state.value,
                )
                with self.assertRaises(TransactionError):
                    engine.execute(prepared.transaction_id)

            self.assertEqual(stages.apply_calls, [])
            self.assertEqual(stages.verify_calls, [])

    def test_resource_lock_contention_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            first = self.consent(engine)
            second = self.consent(engine)

            self.assertTrue(
                engine.locks.acquire("TEST_RESOURCE", first.transaction_id)
            )

            with self.assertRaises(TransactionError) as ctx:
                engine.execute(second.transaction_id)

            self.assertIn("TEST_RESOURCE", str(ctx.exception))
            self.assertIn(first.transaction_id, str(ctx.exception))
            stored = db.get_transaction(second.transaction_id)
            self.assertEqual(stored["state"], "CONSENTED")
            self.assertEqual(stages.apply_calls, [])

            self.assertTrue(
                engine.locks.release("TEST_RESOURCE", first.transaction_id)
            )

    def test_stale_before_state_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(db, self.make_registry(stages))
            prepared = self.consent(engine)

            stages.before_state = {"value": 7}

            with self.assertRaises(TransactionError) as ctx:
                engine.execute(prepared.transaction_id)

            self.assertIn("BeforeState desatualizado", str(ctx.exception))
            self.assertEqual(stages.apply_calls, [])
            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "CONSENTED")
            self.assertEqual(
                stored["error_text"],
                "BeforeState desatualizado na execucao.",
            )

    def test_action_without_executor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            stages = Stages()
            engine = self.make_engine(
                db,
                self.make_registry(stages, apply=None),
            )
            prepared = engine.prepare("TEST_ACTION", {"value": 2})

            self.assertFalse(prepared.execution_enabled)

            consented = engine.consume_consent(
                prepared.transaction_id,
                prepared.consent_token,
            )
            self.assertFalse(consented["execution_enabled"])

            with self.assertRaises(TransactionError) as ctx:
                engine.execute(prepared.transaction_id)

            self.assertIn("Nenhum executor registrado", str(ctx.exception))
            self.assertEqual(stages.apply_calls, [])


class PowerApplyIsolationTests(unittest.TestCase):
    def completed(self):
        return subprocess.CompletedProcess(
            args=["powercfg", "/setactive", VALID_GUID],
            returncode=0,
            stdout="",
            stderr="",
        )

    def test_apply_power_plan_invokes_list_form_without_shell(self):
        with patch(
            "zeus_pc.actions.subprocess.run",
            return_value=self.completed(),
        ) as run:
            result = apply_power_plan(VALID_GUID)

        self.assertTrue(result.ok)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["powercfg", "/setactive", VALID_GUID])
        self.assertNotIn("shell", kwargs)
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn("creationflags", kwargs)

    def test_invalid_guid_returns_failure_without_subprocess(self):
        with patch("zeus_pc.actions.subprocess.run") as run:
            result = apply_power_plan("not-a-guid")

        self.assertFalse(result.ok)
        run.assert_not_called()

    def test_transaction_adapter_returns_evidence_dict(self):
        with patch(
            "zeus_pc.actions.subprocess.run",
            return_value=self.completed(),
        ) as run:
            evidence = _apply_power_plan({"guid": VALID_GUID})

        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["command"], "powercfg /setactive")
        self.assertTrue(evidence["message"])
        args, _kwargs = run.call_args
        self.assertEqual(args[0], ["powercfg", "/setactive", VALID_GUID])

    def test_transaction_adapter_rejects_bad_guid_before_subprocess(self):
        with patch("zeus_pc.actions.subprocess.run") as run:
            evidence = _apply_power_plan({"guid": "nao-eh-guid"})

        self.assertFalse(evidence["ok"])
        self.assertEqual(evidence["message"], "GUID invalido.")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
