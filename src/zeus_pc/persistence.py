from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1


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
