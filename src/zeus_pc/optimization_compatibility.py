from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ProbeResult, Status
from .optimization import Confidence, OptimizationFinding, OptimizationStatus


RISK_RANK = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
}


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    action_id: str
    required_domains: tuple[str, ...]
    allowed_profiles: frozenset[str]
    max_risk: str = "LOW"
    require_reversible: bool = True
    require_high_confidence: bool = True
    security_preserving: bool = False


ACTION_POLICIES: dict[str, ActionPolicy] = {
    "SET_ACTIVE_POWER_PLAN": ActionPolicy(
        action_id="SET_ACTIVE_POWER_PLAN",
        required_domains=("Windows", "Power"),
        allowed_profiles=frozenset(
            {
                "GENERAL",
                "GAMING",
                "GAMING_STREAMING",
                "WORK",
                "EDITING",
                "PROGRAMMING",
            }
        ),
        max_risk="LOW",
        require_reversible=True,
        require_high_confidence=True,
        security_preserving=True,
    ),
}


def _add_blocker(finding: OptimizationFinding, blocker: str) -> None:
    if blocker not in finding.blockers:
        finding.blockers.append(blocker)


def _risk_allowed(actual: str, maximum: str) -> bool:
    actual_rank = RISK_RANK.get(str(actual or "").upper(), 99)
    maximum_rank = RISK_RANK.get(str(maximum or "").upper(), -1)
    return actual_rank <= maximum_rank


def _detect_conflicts(
    findings: list[OptimizationFinding],
) -> list[dict[str, Any]]:
    groups: dict[str, list[OptimizationFinding]] = {}
    for finding in findings:
        if not finding.action_id:
            continue
        groups.setdefault(finding.action_id, []).append(finding)

    conflicts: list[dict[str, Any]] = []
    for action_id, items in groups.items():
        unique_ids = list(dict.fromkeys(item.id for item in items))
        if len(unique_ids) <= 1:
            continue
        conflict_id = f"ACTION_COLLISION:{action_id}"
        for finding in items:
            _add_blocker(finding, conflict_id)
            finding.automation_eligible = False
        conflicts.append(
            {
                "type": "ACTION_COLLISION",
                "action_id": action_id,
                "finding_ids": unique_ids,
                "resolution": "MANUAL_OR_ENGINE_RESOLUTION_REQUIRED",
            }
        )
    return conflicts


def apply_plan_gates(
    findings: list[OptimizationFinding],
    snapshot: list[ProbeResult],
    profile: str,
) -> dict[str, Any]:
    domains = {item.domain: item for item in snapshot}
    conflicts = _detect_conflicts(findings)
    readiness: list[dict[str, Any]] = []
    static_eligible_count = 0
    blocked_action_count = 0
    non_actionable_count = 0

    for finding in findings:
        finding.automation_eligible = False

        if not finding.action_id:
            non_actionable_count += 1
            readiness.append(
                {
                    "finding_id": finding.id,
                    "action_id": None,
                    "state": "NON_ACTIONABLE",
                    "blockers": list(finding.blockers),
                }
            )
            continue

        policy = ACTION_POLICIES.get(finding.action_id)
        if policy is None:
            _add_blocker(finding, "ACTION_NOT_ALLOWLISTED")

        if finding.status != OptimizationStatus.APPLY_RECOMMENDED:
            _add_blocker(finding, "STATUS_NOT_APPLY_RECOMMENDED")

        if policy is not None:
            if profile not in policy.allowed_profiles:
                _add_blocker(finding, "PROFILE_NOT_SUPPORTED")

            for domain_name in policy.required_domains:
                domain = domains.get(domain_name)
                if domain is None:
                    _add_blocker(
                        finding,
                        f"MISSING_REQUIRED_DOMAIN:{domain_name}",
                    )
                elif domain.status != Status.OK:
                    _add_blocker(
                        finding,
                        f"REQUIRED_DOMAIN_NOT_OK:{domain_name}:{domain.status.value}",
                    )

            if (
                policy.require_high_confidence
                and finding.confidence != Confidence.HIGH
            ):
                _add_blocker(finding, "CONFIDENCE_NOT_HIGH")

            if not _risk_allowed(finding.risk, policy.max_risk):
                _add_blocker(finding, "RISK_EXCEEDS_POLICY")

            if policy.require_reversible and not finding.reversible:
                _add_blocker(finding, "REVERSIBILITY_REQUIRED")

            if (
                finding.category == "SECURITY"
                and not policy.security_preserving
            ):
                _add_blocker(
                    finding,
                    "SECURITY_ACTION_NOT_EXPLICITLY_PRESERVING",
                )

        blocking = [
            item
            for item in finding.blockers
            if item != "TRANSACTION_ENGINE_NOT_AVAILABLE"
        ]

        if (
            policy is not None
            and finding.status == OptimizationStatus.APPLY_RECOMMENDED
            and not blocking
        ):
            static_eligible_count += 1
            _add_blocker(
                finding,
                "TRANSACTION_ENGINE_NOT_AVAILABLE",
            )
            readiness.append(
                {
                    "finding_id": finding.id,
                    "action_id": finding.action_id,
                    "state": "STATIC_ELIGIBLE",
                    "blockers": list(finding.blockers),
                }
            )
        else:
            blocked_action_count += 1
            state = (
                "MANUAL_REVIEW"
                if finding.status == OptimizationStatus.MANUAL_REVIEW
                else "BLOCKED"
            )
            readiness.append(
                {
                    "finding_id": finding.id,
                    "action_id": finding.action_id,
                    "state": state,
                    "blockers": list(finding.blockers),
                }
            )

    return {
        "transaction_engine_ready": False,
        "static_eligible_count": static_eligible_count,
        "executable_count": 0,
        "blocked_action_count": blocked_action_count,
        "non_actionable_count": non_actionable_count,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "finding_readiness": readiness,
    }
