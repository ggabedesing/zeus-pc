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
