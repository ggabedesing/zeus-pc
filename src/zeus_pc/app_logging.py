from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StructuredLog:
    def __init__(self, path: str | Path, max_entries: int = 2000) -> None:
        self.path = Path(path)
        self.max_entries = max(int(max_entries), 100)
        self._lock = threading.Lock()

    def write(self, event: str, level: str = "INFO", **context: Any) -> dict[str, Any]:
        event_name = str(event or "").strip()
        if not event_name:
            raise ValueError("event is required")

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": str(level or "INFO").upper(),
            "event": event_name,
            "context": context,
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            self._enforce_retention()

        return record

    def _enforce_retention(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        if len(lines) <= self.max_entries:
            return

        retained = lines[-self.max_entries :]
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text("\n".join(retained) + "\n", encoding="utf-8")
        temp_path.replace(self.path)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), self.max_entries)
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

        records: list[dict[str, Any]] = []
        for line in lines[-safe_limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records
