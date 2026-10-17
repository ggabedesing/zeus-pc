from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .persistence import ZeusDatabase
from .rules import VALID_PROFILES

CONFIG_SCHEMA_VERSION = 1
CONFIG_KEY = "app_config"


@dataclass(frozen=True, slots=True)
class AppConfig:
    schema_version: int = CONFIG_SCHEMA_VERSION
    selected_profile: str = "GENERAL"
    history_limit: int = 20
    observer_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_profile(value: Any) -> str:
    profile = str(value or "GENERAL").upper().strip()
    return profile if profile in VALID_PROFILES else "GENERAL"


def _normalize_history_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 20
    return min(max(limit, 1), 100)


def normalize_config(payload: Any) -> AppConfig:
    if not isinstance(payload, dict):
        return AppConfig()

    version = payload.get("schema_version", CONFIG_SCHEMA_VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = CONFIG_SCHEMA_VERSION

    if version != CONFIG_SCHEMA_VERSION:
        return AppConfig()

    observer_enabled = payload.get("observer_enabled", True)
    if not isinstance(observer_enabled, bool):
        observer_enabled = True

    return AppConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        selected_profile=_normalize_profile(payload.get("selected_profile")),
        history_limit=_normalize_history_limit(payload.get("history_limit", 20)),
        observer_enabled=observer_enabled,
    )


def load_config(database: ZeusDatabase) -> AppConfig:
    database.initialize()
    raw = database.get_setting(CONFIG_KEY)
    config = normalize_config(raw)
    if raw != config.to_dict():
        database.set_setting(CONFIG_KEY, config.to_dict())
    return config


def save_config(database: ZeusDatabase, payload: Any) -> AppConfig:
    current = load_config(database).to_dict()
    if isinstance(payload, dict):
        current.update(payload)
    config = normalize_config(current)
    database.set_setting(CONFIG_KEY, config.to_dict())
    return config
