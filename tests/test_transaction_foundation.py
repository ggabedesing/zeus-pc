import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from zeus_pc.persistence import SCHEMA_VERSION, ZeusDatabase
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    TransactionEngine,
    TransactionError,
    TransactionState,
)


class TransactionFoundationTests(unittest.TestCase):
    def make_registry(self):
        registry = ActionRegistry()

        def validate(args):
            if not isinstance(args, dict) or set(args) != {"value"}:
                raise TransactionError("invalid args")
            return {"value": int(args["value"])}

        registry.register(
            ActionDefinition(
                action_id="TEST_ACTION",
                resource_key="TEST_RESOURCE",
                required_privilege="USER",
                reversible=True,
                validate_args=validate,
                capture_before=lambda args: {"value": 1},
                verify=lambda args: {
                    "ok": args["value"] == 2,
                    "actual": 2,
                },
                rollback_preview=lambda before: {
                    "action_id": "TEST_ACTION",
                    "args": {"value": before["value"]},
                },
            )
        )
        return registry

    def test_prepare_captures_before_state_and_journals_hash_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
                consent_ttl_seconds=60,
            )

            prepared = engine.prepare("test_action", {"value": 2})
            stored = db.get_transaction(
                prepared.transaction_id,
                include_consent_hash=True,
            )

            self.assertEqual(prepared.state, TransactionState.PREPARED)
            self.assertEqual(prepared.args, {"value": 2})
            self.assertEqual(prepared.before_state, {"value": 1})
            self.assertEqual(
                prepared.rollback_preview["args"]["value"],
                1,
            )
            self.assertIsNotNone(stored["consent_hash"])
            self.assertNotEqual(
                stored["consent_hash"],
                prepared.consent_token,
            )
            self.assertEqual(stored["state"], "PREPARED")

    def test_consent_is_one_time_and_plaintext_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
                consent_ttl_seconds=60,
            )
            prepared = engine.prepare("TEST_ACTION", {"value": 2})

            consented = engine.consume_consent(
                prepared.transaction_id,
                prepared.consent_token,
            )

            self.assertEqual(consented["state"], "CONSENTED")
            self.assertFalse(consented["execution_enabled"])
            public = db.get_transaction(prepared.transaction_id)
            private = db.get_transaction(
                prepared.transaction_id,
                include_consent_hash=True,
            )
            self.assertNotIn("consent_hash", public)
            self.assertIsNone(private["consent_hash"])
            with self.assertRaises(TransactionError):
                engine.consume_consent(
                    prepared.transaction_id,
                    prepared.consent_token,
                )

    def test_bad_token_does_not_consume_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
                consent_ttl_seconds=60,
            )
            prepared = engine.prepare("TEST_ACTION", {"value": 2})

            with self.assertRaises(TransactionError):
                engine.consume_consent(
                    prepared.transaction_id,
                    "wrong-token",
                )

            stored = db.get_transaction(prepared.transaction_id)
            self.assertEqual(stored["state"], "PREPARED")
            self.assertIsNone(stored["consent_consumed_at"])

    def test_expired_consent_moves_transaction_to_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
                consent_ttl_seconds=30,
            )
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            later = start + timedelta(seconds=31)

            with patch(
                "zeus_pc.transaction._utc_now",
                side_effect=[start, later],
            ):
                prepared = engine.prepare(
                    "TEST_ACTION",
                    {"value": 2},
                )
                with self.assertRaises(TransactionError):
                    engine.consume_consent(
                        prepared.transaction_id,
                        prepared.consent_token,
                    )

            stored = db.get_transaction(
                prepared.transaction_id,
                include_consent_hash=True,
            )
            self.assertEqual(stored["state"], "EXPIRED")
            self.assertIsNone(stored["consent_hash"])

    def test_unknown_action_and_invalid_args_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
            )

            with self.assertRaises(TransactionError):
                engine.prepare("UNKNOWN", {"value": 2})
            with self.assertRaises(TransactionError):
                engine.prepare(
                    "TEST_ACTION",
                    {"value": 2, "extra": True},
                )

            self.assertEqual(engine.recent(), [])

    def test_verify_interface_is_independent_from_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(
                db,
                registry=self.make_registry(),
            )

            verified = engine.verify_target(
                "TEST_ACTION",
                {"value": 2},
            )

            self.assertTrue(verified["ok"])


class PersistenceMigrationTests(unittest.TestCase):
    def test_schema_v1_data_survives_v2_transaction_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zeus.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', '1')"
                )
                connection.execute(
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
                connection.execute(
                    "INSERT INTO settings(key, value_json) VALUES('legacy', '123')"
                )
                connection.commit()
            finally:
                connection.close()

            db = ZeusDatabase(path)
            health = db.initialize()

            self.assertEqual(health.schema_version, SCHEMA_VERSION)
            self.assertEqual(SCHEMA_VERSION, 3)
            self.assertEqual(db.get_setting("legacy"), 123)
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='transaction_journal'"
                ).fetchone()
            self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
