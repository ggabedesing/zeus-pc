from __future__ import annotations

from .models import ProbeResult, Recommendation
from .rules import evaluate_rules, make_context


def build_recommendations(snapshot: list[ProbeResult], profile: str = "GENERAL") -> list[Recommendation]:
    return evaluate_rules(make_context(snapshot, profile))
