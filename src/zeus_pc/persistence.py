from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 3
_UNSET = object()


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    path: str
    schema_version: int
    writable: bool


class ZeusDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> DatabaseHealth:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    profile TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    recommendations_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transaction_journal (
                    transaction_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    before_state_json TEXT NOT NULL,
                    apply_json TEXT,
                    verify_json TEXT,
                    rollback_json TEXT,
                    error_text TEXT,
                    consent_hash TEXT,
                    consent_expires_at TEXT,
                    consent_consumed_at TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(transaction_journal)"
                ).fetchall()
            }
            if "apply_json" not in columns:
                connection.execute(
                    "ALTER TABLE transaction_journal ADD COLUMN apply_json TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transaction_journal_updated
                ON transaction_journal(updated_at DESC)
                """
            )
            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
        return self.health()

    def health(self) -> DatabaseHealth:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            version = int(row["value"]) if row else 0
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS zeus_write_probe(value INTEGER)"
            )
            connection.execute("DELETE FROM zeus_write_probe")
            connection.execute("INSERT INTO zeus_write_probe(value) VALUES(1)")
        return DatabaseHealth(str(self.path), version, True)

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return default
        return json.loads(row["value_json"])

    def record_scan(
        self,
        profile: str,
        snapshot: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scan_history(profile, snapshot_json, recommendations_json)
                VALUES(?, ?, ?)
                """,
                (
                    profile.upper(),
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(recommendations, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def recent_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 100)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, profile, snapshot_json, recommendations_json
                FROM scan_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "profile": row["profile"],
                "snapshot": json.loads(row["snapshot_json"]),
                "recommendations": json.loads(row["recommendations_json"]),
            }
            for row in rows
        ]

    def record_transaction(
        self,
        *,
        transaction_id: str,
        created_at: str,
        action_id: str,
        state: str,
        args: dict[str, Any],
        before_state: dict[str, Any],
        rollback_preview: dict[str, Any] | None,
        consent_hash: str,
        consent_expires_at: str,
    ) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO transaction_journal(
                    transaction_id,
                    created_at,
                    updated_at,
                    action_id,
                    state,
                    args_json,
                    before_state_json,
                    rollback_json,
                    consent_hash,
                    consent_expires_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    created_at,
                    created_at,
                    action_id,
                    state,
                    json.dumps(args, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(
                        before_state,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    (
                        json.dumps(
                            rollback_preview,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if rollback_preview is not None
                        else None
                    ),
                    consent_hash,
                    consent_expires_at,
                ),
            )

    def update_transaction(
        self,
        transaction_id: str,
        *,
        updated_at: str,
        state: str | None = None,
        expected_state: str | None = None,
        apply: dict[str, Any] | None | object = _UNSET,
        verify: dict[str, Any] | None | object = _UNSET,
        rollback: dict[str, Any] | None | object = _UNSET,
        error_text: str | None | object = _UNSET,
        consent_hash: str | None | object = _UNSET,
        consent_consumed_at: str | None | object = _UNSET,
    ) -> bool:
        assignments = ["updated_at = ?"]
        values: list[Any] = [updated_at]

        if state is not None:
            assignments.append("state = ?")
            values.append(state)
        if apply is not _UNSET:
            assignments.append("apply_json = ?")
            values.append(
                (
                    json.dumps(
                        apply,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if apply is not None
                    else None
                )
            )
        if verify is not _UNSET:
            assignments.append("verify_json = ?")
            values.append(
                (
                    json.dumps(
                        verify,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if verify is not None
                    else None
                )
            )
        if rollback is not _UNSET:
            assignments.append("rollback_json = ?")
            values.append(
                (
                    json.dumps(
                        rollback,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if rollback is not None
                    else None
                )
            )
        if error_text is not _UNSET:
            assignments.append("error_text = ?")
            values.append(error_text)
        if consent_hash is not _UNSET:
            assignments.append("consent_hash = ?")
            values.append(consent_hash)
        if consent_consumed_at is not _UNSET:
            assignments.append("consent_consumed_at = ?")
            values.append(consent_consumed_at)

        values.append(transaction_id)
        guard = ""
        if expected_state is not None:
            guard = "AND state = ?"
            values.append(expected_state)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE transaction_journal
                SET {", ".join(assignments)}
                WHERE transaction_id = ?
                {guard}
                """,
                values,
            )
            return cursor.rowcount == 1

    def consume_transaction_consent(
        self,
        transaction_id: str,
        *,
        expected_hash: str,
        updated_at: str,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE transaction_journal
                SET state = 'CONSENTED',
                    updated_at = ?,
                    consent_consumed_at = ?,
                    consent_hash = NULL
                WHERE transaction_id = ?
                  AND state = 'PREPARED'
                  AND consent_hash = ?
                """,
                (
                    updated_at,
                    updated_at,
                    transaction_id,
                    expected_hash,
                ),
            )
            return cursor.rowcount == 1

    def get_transaction(
        self,
        transaction_id: str,
        *,
        include_consent_hash: bool = False,
    ) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM transaction_journal
                WHERE transaction_id = ?
                """,
                (transaction_id,),
            ).fetchone()
        if row is None:
            return None
        return self._transaction_row(
            row,
            include_consent_hash=include_consent_hash,
        )

    def recent_transactions(
        self,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.initialize()
        safe_limit = min(max(int(limit), 1), 100)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM transaction_journal
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            self._transaction_row(row, include_consent_hash=False)
            for row in rows
        ]

    @staticmethod
    def _transaction_row(
        row: sqlite3.Row,
        *,
        include_consent_hash: bool,
    ) -> dict[str, Any]:
        payload = {
            "transaction_id": row["transaction_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "action_id": row["action_id"],
            "state": row["state"],
            "args": json.loads(row["args_json"]),
            "before_state": json.loads(row["before_state_json"]),
            "apply": (
                json.loads(row["apply_json"])
                if row["apply_json"]
                else None
            ),
            "verify": (
                json.loads(row["verify_json"])
                if row["verify_json"]
                else None
            ),
            "rollback": (
                json.loads(row["rollback_json"])
                if row["rollback_json"]
                else None
            ),
            "error_text": row["error_text"],
            "consent_expires_at": row["consent_expires_at"],
            "consent_consumed_at": row["consent_consumed_at"],
        }
        if include_consent_hash:
            payload["consent_hash"] = row["consent_hash"]
        return payload
