from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 4
_UNSET = object()


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    path: str
    schema_version: int
    writable: bool


class ZeusDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._schema_ready = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        # Concurrencia real (increment 020): leitores nao bloqueiam o
        # escritor em WAL e escrituras aguardam em vez de falhar.
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass  # filesystems sem suporte (ex.: rede) caem no journal padrao
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        """Initialize once per instance; reads stop rewriting the schema."""
        if not self._schema_ready:
            self.initialize()

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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transaction_journal_updated
                ON transaction_journal(updated_at DESC)
                """
            )
            try:
                connection.execute(
                    "ALTER TABLE transaction_journal ADD COLUMN apply_json TEXT"
                )
            except sqlite3.OperationalError:
                pass
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_installs (
                    model_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    license_name TEXT NOT NULL,
                    license_accepted_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    task TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    elapsed_ms REAL NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    tokens_per_s REAL NOT NULL,
                    fidelity REAL NOT NULL,
                    score REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_benchmarks_model
                ON model_benchmarks(model_id, id DESC)
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
        self._schema_ready = True
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
        self._ensure_schema()
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
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE transaction_journal
                SET {", ".join(assignments)}
                WHERE transaction_id = ?
                """,
                values,
            )
            return cursor.rowcount == 1

    def transition_transaction(
        self,
        transaction_id: str,
        *,
        from_state: str,
        to_state: str,
        updated_at: str,
        apply_result: dict[str, Any] | None | object = _UNSET,
        verify: dict[str, Any] | None | object = _UNSET,
        rollback: dict[str, Any] | None | object = _UNSET,
        error_text: str | None | object = _UNSET,
    ) -> bool:
        assignments = ["state = ?", "updated_at = ?"]
        values: list[Any] = [to_state, updated_at]

        if apply_result is not _UNSET:
            assignments.append("apply_json = ?")
            values.append(
                (
                    json.dumps(
                        apply_result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if apply_result is not None
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

        values.extend([transaction_id, from_state])
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE transaction_journal
                SET {", ".join(assignments)}
                WHERE transaction_id = ? AND state = ?
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
        self._ensure_schema()
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
        self._ensure_schema()
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

    def record_model_install(
        self,
        *,
        model_id: str,
        filename: str,
        license_name: str,
        license_accepted_at: str,
        sha256: str,
        size_bytes: int,
        source: str,
        source_url: str = "",
    ) -> None:
        """Register (or re-register) one installed model with its hash."""
        self._ensure_schema()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_installs(
                    model_id, filename, license_name, license_accepted_at,
                    sha256, size_bytes, source, source_url, installed_at,
                    last_used_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT(model_id) DO UPDATE SET
                    filename = excluded.filename,
                    license_name = excluded.license_name,
                    license_accepted_at = excluded.license_accepted_at,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    installed_at = CURRENT_TIMESTAMP,
                    last_used_at = NULL
                """,
                (
                    model_id,
                    filename,
                    license_name,
                    license_accepted_at,
                    sha256,
                    int(size_bytes),
                    source,
                    source_url,
                ),
            )

    def get_model_install(self, model_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_installs WHERE model_id = ?",
                (str(model_id or "").strip().upper(),),
            ).fetchone()
        return self._install_row(row) if row is not None else None

    def list_model_installs(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_installs ORDER BY model_id"
            ).fetchall()
        return [self._install_row(row) for row in rows]

    def touch_model_used(
        self,
        model_id: str,
        *,
        used_at: str | None = None,
    ) -> bool:
        """Mark a model as most recently used (feeds the LRU order)."""
        self._ensure_schema()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE model_installs
                SET last_used_at = COALESCE(?, CURRENT_TIMESTAMP)
                WHERE model_id = ?
                """,
                (used_at, str(model_id or "").strip().upper()),
            )
            return cursor.rowcount == 1

    def delete_model_install(self, model_id: str) -> bool:
        self._ensure_schema()
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM model_installs WHERE model_id = ?",
                (str(model_id or "").strip().upper(),),
            )
            return cursor.rowcount == 1

    def record_benchmark(
        self,
        *,
        model_id: str,
        language: str,
        task: str,
        elapsed_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        tokens_per_s: float,
        fidelity: float,
        score: float,
    ) -> int:
        self._ensure_schema()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO model_benchmarks(
                    model_id, language, task, elapsed_ms, prompt_tokens,
                    completion_tokens, tokens_per_s, fidelity, score
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(model_id).strip().upper(),
                    str(language).strip().lower(),
                    str(task).strip().upper(),
                    float(elapsed_ms),
                    int(prompt_tokens),
                    int(completion_tokens),
                    float(tokens_per_s),
                    float(fidelity),
                    float(score),
                ),
            )
            return int(cursor.lastrowid)

    def latest_benchmarks(
        self,
        model_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = min(max(int(limit), 1), 500)
        query = (
            "SELECT * FROM model_benchmarks"
            " ORDER BY id DESC LIMIT ?"
        )
        values: list[Any] = [safe_limit]
        if model_id:
            query = (
                "SELECT * FROM model_benchmarks WHERE model_id = ?"
                " ORDER BY id DESC LIMIT ?"
            )
            values = [str(model_id).strip().upper(), safe_limit]
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._benchmark_row(row) for row in rows]

    def benchmark_scores(self) -> dict[str, float]:
        """Latest score per (model, language), averaged per model.

        Deterministic given the database contents: the newest row per
        language wins, then a plain mean rounded to two decimals.
        """
        self._ensure_schema()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT b.model_id, b.language, b.score
                FROM model_benchmarks b
                WHERE b.id = (
                    SELECT MAX(x.id) FROM model_benchmarks x
                    WHERE x.model_id = b.model_id
                      AND x.language = b.language
                )
                ORDER BY b.model_id, b.language
                """
            ).fetchall()
        grouped: dict[str, list[float]] = {}
        for row in rows:
            grouped.setdefault(row["model_id"], []).append(
                float(row["score"])
            )
        return {
            model_id: round(sum(values) / len(values), 2)
            for model_id, values in sorted(grouped.items())
        }

    @staticmethod
    def _install_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "model_id": row["model_id"],
            "filename": row["filename"],
            "license_name": row["license_name"],
            "license_accepted_at": row["license_accepted_at"],
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
            "source": row["source"],
            "source_url": row["source_url"],
            "installed_at": row["installed_at"],
            "last_used_at": row["last_used_at"],
        }

    @staticmethod
    def _benchmark_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "model_id": row["model_id"],
            "language": row["language"],
            "task": row["task"],
            "created_at": row["created_at"],
            "elapsed_ms": float(row["elapsed_ms"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "tokens_per_s": float(row["tokens_per_s"]),
            "fidelity": float(row["fidelity"]),
            "score": float(row["score"]),
        }

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
