import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from zeus_pc.persistence import SCHEMA_VERSION, ZeusDatabase
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    TransactionEngine,
    TransactionError,
)


def make_registry(
    *,
    verify_ok=True,
    apply_ok=True,
    rollback_ok=True,
    counters=None,
):
    counters = counters if counters is not None else {}
    registry = ActionRegistry()

    def validate(args):
        if not isinstance(args, dict) or set(args) != {"value"}:
            raise TransactionError("invalid args")
        return {"value": int(args["value"])}

    def apply(args):
        counters["apply"] = counters.get("apply", 0) + 1
        if not apply_ok:
            return {"ok": False, "message": "apply reprovado."}
        return {"ok": True, "message": f"applied {args['value']}"}

    def verify(args):
        counters["verify"] = counters.get("verify", 0) + 1
        return {"ok": bool(verify_ok), "actual": args["value"]}

    def rollback(before_state, plan):
        counters["rollback"] = counters.get("rollback", 0) + 1
        counters["rollback_args"] = (dict(before_state), dict(plan))
        if not rollback_ok:
            return {"ok": False, "message": "rollback reprovado."}
        return {"ok": True, "message": "restored"}

    registry.register(
        ActionDefinition(
            action_id="TEST_ACTION",
            resource_key="TEST_RESOURCE",
            required_privilege="USER",
            reversible=True,
            validate_args=validate,
            capture_before=lambda args: {"value": 1},
            verify=verify,
            rollback_preview=lambda before: {
                "action_id": "TEST_ACTION",
                "args": {"value": before["value"]},
            },
            apply=apply,
            rollback=rollback,
        )
    )
    return registry


def prepare_and_consent(engine, action_id="TEST_ACTION", args=None):
    prepared = engine.prepare(action_id, args or {"value": 2})
    engine.consume_consent(prepared.transaction_id, prepared.consent_token)
    return prepared.transaction_id


class TransactionExecutionTests(unittest.TestCase):
    def make_engine(self, db, **kwargs):
        registry = kwargs.pop("registry", None)
        if registry is None:
            registry = make_registry(**kwargs.pop("flags", {}))
        return TransactionEngine(db, registry=registry)

    def test_consent_to_verified_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = self.make_engine(db)
            txid = prepare_and_consent(engine)

            result = engine.execute(txid)

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "VERIFIED")
            stored = db.get_transaction(txid, include_consent_hash=True)
            self.assertEqual(stored["state"], "VERIFIED")
            self.assertIsNotNone(stored["apply"])
            self.assertTrue(stored["apply"]["ok"])
            self.assertTrue(stored["verify"]["ok"])
            self.assertIsNone(stored["consent_hash"])
            self.assertIsNone(stored["error_text"])

    def test_failed_verify_triggers_automatic_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            counters = {}
            engine = self.make_engine(
                db,
                flags={"verify_ok": False, "counters": counters},
            )
            txid = prepare_and_consent(engine)

            result = engine.execute(txid)

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "ROLLED_BACK")
            stored = db.get_transaction(txid)
            self.assertEqual(stored["state"], "ROLLED_BACK")
            self.assertFalse(stored["verify"]["ok"])
            self.assertTrue(stored["rollback"]["ok"])
            self.assertIn("rollback", stored["error_text"])
            # rollback receives the captured BeforeState and preview.
            before_state, plan = counters["rollback_args"]
            self.assertEqual(before_state, {"value": 1})
            self.assertEqual(plan["args"], {"value": 1})

    def test_failed_apply_marks_transaction_failed_without_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            counters = {}
            engine = self.make_engine(
                db,
                flags={"apply_ok": False, "counters": counters},
            )
            txid = prepare_and_consent(engine)

            result = engine.execute(txid)

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "FAILED")
            stored = db.get_transaction(txid)
            self.assertEqual(stored["state"], "FAILED")
            self.assertFalse(stored["apply"]["ok"])
            self.assertIsNone(stored["verify"])
            self.assertEqual(stored["error_text"], "apply reprovado.")
            self.assertNotIn("verify", counters)
            self.assertNotIn("rollback", counters)

    def test_failed_rollback_lands_in_failed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = self.make_engine(
                db,
                flags={"verify_ok": False, "rollback_ok": False},
            )
            txid = prepare_and_consent(engine)

            result = engine.execute(txid)

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "FAILED")
            stored = db.get_transaction(txid)
            self.assertFalse(stored["rollback"]["ok"])
            self.assertIn("ROLLBACK falharam", stored["error_text"])

    def test_execute_requires_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = self.make_engine(db)

            prepared = engine.prepare("TEST_ACTION", {"value": 2})

            with self.assertRaises(TransactionError):
                engine.execute(prepared.transaction_id)
            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "PREPARED")
            self.assertIsNone(stored["apply"])

    def test_execute_is_single_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            counts = {"apply": 0, "verify": 0}
            registry = make_registry(counters=counts)
            engine = TransactionEngine(db, registry=registry)
            txid = prepare_and_consent(engine)

            engine.execute(txid)
            with self.assertRaises(TransactionError):
                engine.execute(txid)

            self.assertEqual(counts["apply"], 1)
            self.assertEqual(counts["verify"], 1)

    def test_action_without_apply_stays_consent_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            registry = ActionRegistry()
            registry.register(
                ActionDefinition(
                    action_id="PREPARE_ONLY",
                    resource_key="OTHER_RESOURCE",
                    required_privilege="USER",
                    reversible=True,
                    validate_args=lambda args: dict(args),
                    capture_before=lambda args: {"value": 1},
                    verify=lambda args: {"ok": True},
                    rollback_preview=lambda before: None,
                )
            )
            engine = TransactionEngine(db, registry=registry)
            prepared = engine.prepare("PREPARE_ONLY", {})
            engine.consume_consent(
                prepared.transaction_id,
                prepared.consent_token,
            )

            with self.assertRaises(TransactionError):
                engine.execute(prepared.transaction_id)

            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "CONSENTED")
            self.assertEqual(engine.executable_ids(), [])

    def test_resource_lock_serializes_same_resource_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            first_started = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()

            owner = {}

            def guarded_apply(args):
                if owner.get("tx") is None:
                    owner["tx"] = "first"
                    first_started.set()
                    release_first.wait(timeout=5)
                else:
                    second_started.set()
                return {"ok": True, "message": "applied"}

            registry = ActionRegistry()
            registry.register(
                ActionDefinition(
                    action_id="LOCKED_ACTION",
                    resource_key="SHARED_RESOURCE",
                    required_privilege="USER",
                    reversible=True,
                    validate_args=lambda args: dict(args),
                    capture_before=lambda args: {"value": 0},
                    verify=lambda args: {"ok": True},
                    rollback_preview=lambda before: None,
                    apply=guarded_apply,
                    rollback=lambda before, plan: {"ok": True},
                )
            )
            engine = TransactionEngine(db, registry=registry)
            first = prepare_and_consent(engine, "LOCKED_ACTION", {})
            second = prepare_and_consent(engine, "LOCKED_ACTION", {})

            errors = []

            def run(txid):
                try:
                    engine.execute(txid)
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            worker_one = threading.Thread(target=run, args=(first,))
            worker_two = threading.Thread(target=run, args=(second,))
            worker_one.start()
            self.assertTrue(first_started.wait(timeout=5))
            worker_two.start()
            self.assertFalse(second_started.wait(timeout=0.3))
            release_first.set()
            worker_one.join(timeout=5)
            worker_two.join(timeout=5)

            self.assertTrue(second_started.is_set())
            self.assertEqual(errors, [])
            self.assertEqual(
                db.get_transaction(first)["state"], "VERIFIED"
            )
            self.assertEqual(
                db.get_transaction(second)["state"], "VERIFIED"
            )


class SchemaMigrationV3Tests(unittest.TestCase):
    def test_v2_journal_row_survives_apply_column_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zeus.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', '2')"
                )
                connection.execute(
                    """
                    CREATE TABLE transaction_journal (
                        transaction_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        action_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        args_json TEXT NOT NULL,
                        before_state_json TEXT NOT NULL,
                        verify_json TEXT,
                        rollback_json TEXT,
                        error_text TEXT,
                        consent_hash TEXT,
                        consent_expires_at TEXT,
                        consent_consumed_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO transaction_journal(
                        transaction_id, created_at, updated_at, action_id,
                        state, args_json, before_state_json
                    ) VALUES('legacy-1', '2026-01-01T00:00:00Z',
                             '2026-01-01T00:00:00Z', 'SET_ACTIVE_POWER_PLAN',
                             'CONSENTED', '{"guid":"a"}', '{"active_guid":"b"}')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            db = ZeusDatabase(path)
            health = db.initialize()

            self.assertEqual(health.schema_version, SCHEMA_VERSION)
            self.assertEqual(SCHEMA_VERSION, 3)
            stored = db.get_transaction("legacy-1")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["state"], "CONSENTED")
            self.assertIsNone(stored["apply"])
            # idempotent re-initialize must not fail on the added column
            db.initialize()
            self.assertIsNotNone(db.get_transaction("legacy-1"))


if __name__ == "__main__":
    unittest.main()