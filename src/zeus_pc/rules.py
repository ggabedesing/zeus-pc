from dataclasses import dataclass
from typing import Any
from .models import ProbeResult

@dataclass(frozen=True, slots=True)
class RuleContext:
    domains: dict[str, ProbeResult]
    profile: str = "GENERAL"

    def metric(self, domain: str, name: str, default: Any = None) -> Any:
        result = self.domains.get(domain)
        if result is None:
            return default
        for item in result.metrics:
            if item.name == name:
                return item.value
        return default

def make_context(snapshot: list[ProbeResult], profile: str = "GENERAL") -> RuleContext:
    return RuleContext({item.domain: item for item in snapshot}, profile.upper())
