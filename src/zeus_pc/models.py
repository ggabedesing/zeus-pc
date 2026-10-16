from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    REQUIRES_ELEVATION = "REQUIRES_ELEVATION"
    NOT_CHECKED = "NOT_CHECKED"
    FAILED = "FAILED"


@dataclass(slots=True)
class Metric:
    name: str
    value: Any = None
    unit: str | None = None
    source: str = ""
    confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProbeResult:
    domain: str
    status: Status
    metrics: list[Metric] = field(default_factory=list)
    message: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "status": self.status.value,
            "metrics": [item.to_dict() for item in self.metrics],
            "message": self.message,
            "source": self.source,
        }


@dataclass(slots=True)
class Recommendation:
    id: str
    title: str
    category: str
    status: str
    reason: str
    benefit: str
    risk: str = "LOW"
    reversible: bool = True
    action_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ELEVATION_TOKENS = (
    "access is denied",
    "acesso negado",
    "eleva",
    "elevat",
    "administ",
    "permiss",
)
TIMEOUT_TOKENS = ("tempo esgotado", "timed out", "timeout")
UNSUPPORTED_TOKENS = (
    "is not recognized",
    "não é reconhecido",
    "nao e reconhecido",
    "commandnotfound",
    "invalid namespace",
    "namespace inexistente",
    "not available",
    "nao esta disponivel",
)


def humanize_probe_error(
    exc: BaseException,
    source: str,
    default_status: Status = Status.FAILED,
) -> tuple[Status, str]:
    text = str(exc).lower()
    if any(token in text for token in ELEVATION_TOKENS):
        return Status.REQUIRES_ELEVATION, "A leitura exige administrador."
    if any(token in text for token in TIMEOUT_TOKENS):
        return Status.PARTIAL, f"Tempo esgotado ao consultar {source}."
    if any(token in text for token in UNSUPPORTED_TOKENS):
        return Status.UNSUPPORTED, f"{source} nao esta disponivel nesta edicao do Windows."
    return default_status, f"Nao foi possivel ler {source}."


def demote_if_unknown(
    result: ProbeResult,
    meaningful: set[str] | tuple[str, ...] | None = None,
    fallback_status: Status = Status.PARTIAL,
) -> ProbeResult:
    if result.status is not Status.OK:
        return result
    names = set(meaningful) if meaningful is not None else {item.name for item in result.metrics}
    if any(item.value is not None for item in result.metrics if item.name in names):
        return result
    label = result.source or result.domain
    result.status = fallback_status
    result.message = f"Nenhum valor utilizavel foi lido de {label}."
    return result
