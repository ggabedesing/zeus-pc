from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .models import Metric, ProbeResult, Recommendation, Status
from .rules import evaluate_rules, make_context


class OptimizationStatus(StrEnum):
    APPLY_RECOMMENDED = "APPLY_RECOMMENDED"
    ALREADY_CORRECT = "ALREADY_CORRECT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NO_ACTION = "NO_ACTION"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(slots=True)
class OptimizationEvidence:
    domain: str
    metric: str
    value: Any
    source: str = ""
    confidence: Confidence = Confidence.MEDIUM

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = self.confidence.value
        return payload


@dataclass(slots=True)
class OptimizationFinding:
    id: str
    title: str
    category: str
    status: OptimizationStatus
    reason: str
    benefit: str
    confidence: Confidence
    risk: str = "LOW"
    reversible: bool = True
    action_id: str | None = None
    automation_eligible: bool = False
    evidence: list[OptimizationEvidence] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "status": self.status.value,
            "reason": self.reason,
            "benefit": self.benefit,
            "confidence": self.confidence.value,
            "risk": self.risk,
            "reversible": self.reversible,
            "action_id": self.action_id,
            "automation_eligible": self.automation_eligible,
            "evidence": [item.to_dict() for item in self.evidence],
            "blockers": list(self.blockers),
        }


@dataclass(slots=True)
class OptimizationPlan:
    plan_id: str
    created_at: str
    profile: str
    mode: str
    execution_ready: bool
    findings: list[OptimizationFinding]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "profile": self.profile,
            "mode": self.mode,
            "execution_ready": self.execution_ready,
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
        }


RULE_EVIDENCE: dict[str, tuple[tuple[str, str], ...]] = {
    "storage.low-space": (("Storage", "volumes"),),
    "memory.pressure.v1": (
        ("RAM", "available_gb"),
        ("RAM", "usable_gb"),
    ),
    "devices.problem-detected.v1": (("Devices", "problem_count"),),
    "drivers.unsigned-review.v1": (("Drivers", "unsigned_count"),),
    "windows.pending-reboot.v1": (("Integrity", "pending_reboot"),),
    "windows.pending-updates.v1": (("Windows Update", "pending_count"),),
    "startup.review.v2": (("Startup", "items"),),
    "power.profile-alignment.v1": (("Power", "active_scheme"),),
}

CORE_COVERAGE_DOMAINS = {
    "Windows",
    "CPU",
    "RAM",
    "Storage",
    "Power",
    "Startup",
    "Windows Update",
}


def snapshot_from_payload(payload: list[dict[str, Any]]) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for item in payload:
        try:
            status = Status(str(item.get("status") or Status.FAILED.value))
        except ValueError:
            status = Status.FAILED
        metrics = []
        for metric in item.get("metrics") or []:
            metrics.append(
                Metric(
                    name=str(metric.get("name") or ""),
                    value=metric.get("value"),
                    unit=metric.get("unit"),
                    source=str(metric.get("source") or ""),
                    confidence=metric.get("confidence"),
                )
            )
        results.append(
            ProbeResult(
                domain=str(item.get("domain") or "Unknown"),
                status=status,
                metrics=metrics,
                message=str(item.get("message") or ""),
                source=str(item.get("source") or ""),
            )
        )
    return results


def _domain_map(snapshot: list[ProbeResult]) -> dict[str, ProbeResult]:
    return {item.domain: item for item in snapshot}


def _confidence_for_domain(
    domains: dict[str, ProbeResult],
    domain: str | None,
) -> Confidence:
    if not domain:
        return Confidence.MEDIUM
    result = domains.get(domain)
    if result is None:
        return Confidence.LOW
    if result.status == Status.OK:
        return Confidence.HIGH
    if result.status in {Status.PARTIAL, Status.NOT_CHECKED}:
        return Confidence.MEDIUM
    return Confidence.LOW


def _evidence_for_recommendation(
    recommendation: Recommendation,
    domains: dict[str, ProbeResult],
) -> list[OptimizationEvidence]:
    specs = RULE_EVIDENCE.get(recommendation.id, ())
    evidence: list[OptimizationEvidence] = []
    for domain_name, metric_name in specs:
        result = domains.get(domain_name)
        if result is None:
            continue
        metric = next(
            (item for item in result.metrics if item.name == metric_name),
            None,
        )
        if metric is None:
            continue

        value = metric.value
        normalized_metric = metric_name
        if recommendation.id == "startup.review.v2" and isinstance(value, list):
            value = len(value)
            normalized_metric = "item_count"
        elif recommendation.id == "storage.low-space" and isinstance(value, list):
            system_volume = next(
                (
                    item
                    for item in value
                    if str(item.get("root") or "").upper().startswith("C:")
                ),
                None,
            )
            value = system_volume
            normalized_metric = "system_volume"

        evidence.append(
            OptimizationEvidence(
                domain=domain_name,
                metric=normalized_metric,
                value=value,
                source=metric.source or result.source,
                confidence=_confidence_for_domain(domains, domain_name),
            )
        )

    if not evidence:
        evidence.append(
            OptimizationEvidence(
                domain="Rule Engine",
                metric=recommendation.id,
                value=recommendation.reason,
                source="contextual rule",
                confidence=Confidence.MEDIUM,
            )
        )
    return evidence


def _formal_status(recommendation: Recommendation) -> OptimizationStatus:
    normalized = str(recommendation.status or "").upper()
    if normalized == "RECOMMENDED":
        return OptimizationStatus.APPLY_RECOMMENDED
    if normalized == "MANUAL_REVIEW":
        return OptimizationStatus.MANUAL_REVIEW
    if normalized == "ALREADY_CORRECT":
        return OptimizationStatus.ALREADY_CORRECT
    if normalized == "NOT_APPLICABLE":
        return OptimizationStatus.NOT_APPLICABLE
    if normalized == "BLOCKED":
        return OptimizationStatus.BLOCKED
    if normalized == "NO_ACTION":
        return OptimizationStatus.NO_ACTION
    return OptimizationStatus.MANUAL_REVIEW


def _finding_from_recommendation(
    recommendation: Recommendation,
    domains: dict[str, ProbeResult],
) -> OptimizationFinding:
    evidence = _evidence_for_recommendation(recommendation, domains)
    confidence = min(
        (item.confidence for item in evidence),
        key=lambda value: {
            Confidence.LOW: 0,
            Confidence.MEDIUM: 1,
            Confidence.HIGH: 2,
        }[value],
    )
    status = _formal_status(recommendation)
    eligible = bool(
        status == OptimizationStatus.APPLY_RECOMMENDED
        and recommendation.action_id
        and recommendation.risk == "LOW"
        and recommendation.reversible
        and confidence == Confidence.HIGH
    )
    blockers: list[str] = []
    if status == OptimizationStatus.APPLY_RECOMMENDED and not recommendation.action_id:
        blockers.append("NO_ALLOWLISTED_ACTION")
    if status == OptimizationStatus.APPLY_RECOMMENDED and not recommendation.reversible:
        blockers.append("NOT_REVERSIBLE")
    if confidence != Confidence.HIGH:
        blockers.append("CONFIDENCE_NOT_HIGH")

    return OptimizationFinding(
        id=recommendation.id,
        title=recommendation.title,
        category=recommendation.category,
        status=status,
        reason=recommendation.reason,
        benefit=recommendation.benefit,
        confidence=confidence,
        risk=recommendation.risk,
        reversible=recommendation.reversible,
        action_id=recommendation.action_id,
        automation_eligible=eligible,
        evidence=evidence,
        blockers=blockers,
    )


def _summary_metric(
    baseline: dict[str, Any] | None,
    name: str,
) -> dict[str, Any] | None:
    if not isinstance(baseline, dict):
        return None
    metrics = baseline.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(name)
    return value if isinstance(value, dict) else None


def _observer_confidence(sample_count: int) -> Confidence:
    if sample_count >= 10:
        return Confidence.HIGH
    if sample_count >= 3:
        return Confidence.MEDIUM
    return Confidence.LOW


def _process_payload(process_snapshot: Any) -> dict[str, Any] | None:
    if process_snapshot is None:
        return None
    if isinstance(process_snapshot, dict):
        return process_snapshot
    to_dict = getattr(process_snapshot, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return value if isinstance(value, dict) else None
    return None


def _top_process_evidence(
    process_snapshot: Any,
    field: str,
) -> OptimizationEvidence | None:
    payload = _process_payload(process_snapshot)
    if not payload:
        return None
    items = payload.get(field)
    if not isinstance(items, list) or not items:
        return None
    top = items[0]
    if not isinstance(top, dict):
        return None
    return OptimizationEvidence(
        domain="Observer Processes",
        metric=field,
        value={
            "pid": top.get("pid"),
            "name": top.get("name"),
            "cpu_pct": top.get("cpu_pct"),
            "working_set_bytes": top.get("working_set_bytes"),
        },
        source="native process observer",
        confidence=Confidence.MEDIUM,
    )


def _observer_findings(
    profile: str,
    baseline: dict[str, Any] | None,
    process_snapshot: Any,
) -> list[OptimizationFinding]:
    findings: list[OptimizationFinding] = []

    cpu = _summary_metric(baseline, "cpu_pct")
    if cpu and int(cpu.get("count") or 0) >= 3 and float(cpu.get("avg") or 0) >= 85:
        count = int(cpu["count"])
        confidence = _observer_confidence(count)
        evidence = [
            OptimizationEvidence(
                domain="Observer",
                metric="cpu_pct",
                value=cpu,
                source="Observer baseline",
                confidence=confidence,
            )
        ]
        top = _top_process_evidence(process_snapshot, "top_cpu")
        if top:
            evidence.append(top)
        findings.append(
            OptimizationFinding(
                id="observer.cpu-sustained-pressure.v1",
                title="Carga de CPU sustentada detectada",
                category="PERFORMANCE",
                status=OptimizationStatus.MANUAL_REVIEW,
                reason=(
                    f"A média de CPU foi {float(cpu.get('avg') or 0):.1f}% "
                    f"em {count} amostras. O Zeus precisa correlacionar a carga "
                    "com o processo e a sessão antes de propor qualquer ajuste."
                ),
                benefit="PERFORMANCE_STABILITY",
                confidence=confidence,
                risk="LOW",
                reversible=True,
                evidence=evidence,
                blockers=["NO_SAFE_AUTOMATIC_ACTION"],
            )
        )

    ram = _summary_metric(baseline, "ram_used_pct")
    if ram and int(ram.get("count") or 0) >= 3 and float(ram.get("avg") or 0) >= 90:
        count = int(ram["count"])
        confidence = _observer_confidence(count)
        evidence = [
            OptimizationEvidence(
                domain="Observer",
                metric="ram_used_pct",
                value=ram,
                source="Observer baseline",
                confidence=confidence,
            )
        ]
        top = _top_process_evidence(process_snapshot, "top_memory")
        if top:
            evidence.append(top)
        findings.append(
            OptimizationFinding(
                id="observer.memory-sustained-pressure.v1",
                title="Uso de memória sustentado muito alto",
                category="PERFORMANCE",
                status=OptimizationStatus.MANUAL_REVIEW,
                reason=(
                    f"A média de RAM utilizada foi {float(ram.get('avg') or 0):.1f}% "
                    f"em {count} amostras. O processo consumidor deve ser validado "
                    "antes de fechar aplicações ou alterar paginação."
                ),
                benefit="STABILITY",
                confidence=confidence,
                risk="LOW",
                reversible=True,
                evidence=evidence,
                blockers=["NO_SAFE_AUTOMATIC_ACTION"],
            )
        )

    gpu = _summary_metric(baseline, "gpu_pct")
    if (
        profile in {"GAMING", "GAMING_STREAMING"}
        and gpu
        and int(gpu.get("count") or 0) >= 3
        and float(gpu.get("avg") or 0) >= 95
    ):
        count = int(gpu["count"])
        confidence = _observer_confidence(count)
        findings.append(
            OptimizationFinding(
                id="observer.gpu-bound-explanation.v1",
                title="Carga de GPU próxima do limite",
                category="GAMING",
                status=OptimizationStatus.NO_ACTION,
                reason=(
                    f"A GPU ficou em média em {float(gpu.get('avg') or 0):.1f}% "
                    f"em {count} amostras. Isso pode representar um workload "
                    "GPU-bound normal; não é motivo suficiente para aplicar tweak."
                ),
                benefit="PERFORMANCE",
                confidence=confidence,
                risk="LOW",
                reversible=True,
                evidence=[
                    OptimizationEvidence(
                        domain="Observer",
                        metric="gpu_pct",
                        value=gpu,
                        source="Observer baseline",
                        confidence=confidence,
                    )
                ],
                blockers=["MEASURE_FRAMETIME_BEFORE_ACTION"],
            )
        )

    return findings


def _coverage_blocker(
    snapshot: list[ProbeResult],
) -> OptimizationFinding | None:
    unavailable = [
        item
        for item in snapshot
        if item.domain in CORE_COVERAGE_DOMAINS
        and item.status
        in {
            Status.FAILED,
            Status.UNSUPPORTED,
            Status.REQUIRES_ELEVATION,
        }
    ]
    if not unavailable:
        return None
    return OptimizationFinding(
        id="system.coverage-limited.v1",
        title="Diagnóstico insuficiente para concluir que nada precisa mudar",
        category="SYSTEM",
        status=OptimizationStatus.BLOCKED,
        reason=(
            "Um ou mais domínios essenciais não puderam ser confirmados; "
            "o plano mantém essas lacunas explícitas em vez de assumir saúde."
        ),
        benefit="STABILITY",
        confidence=Confidence.HIGH,
        risk="LOW",
        reversible=True,
        evidence=[
            OptimizationEvidence(
                domain=item.domain,
                metric="probe_status",
                value=item.status.value,
                source=item.source,
                confidence=Confidence.HIGH,
            )
            for item in unavailable
        ],
        blockers=["INCOMPLETE_CORE_DIAGNOSTICS"],
    )


def _deduplicate(findings: list[OptimizationFinding]) -> list[OptimizationFinding]:
    output: list[OptimizationFinding] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.id in seen:
            continue
        seen.add(finding.id)
        output.append(finding)
    return output


def build_optimization_plan(
    snapshot: list[ProbeResult],
    profile: str = "GENERAL",
    observer_baseline: dict[str, Any] | None = None,
    process_snapshot: Any = None,
) -> OptimizationPlan:
    context = make_context(snapshot, profile)
    domains = _domain_map(snapshot)
    recommendations = evaluate_rules(context)

    findings = [
        _finding_from_recommendation(item, domains)
        for item in recommendations
    ]
    findings.extend(
        _observer_findings(
            context.profile,
            observer_baseline,
            process_snapshot,
        )
    )

    non_no_action = [
        item
        for item in findings
        if item.id != "system.no-action.v1"
    ]
    if non_no_action:
        findings = non_no_action

    if not findings:
        coverage = _coverage_blocker(snapshot)
        if coverage is not None:
            findings = [coverage]
    elif (
        len(findings) == 1
        and findings[0].id == "system.no-action.v1"
    ):
        coverage = _coverage_blocker(snapshot)
        if coverage is not None:
            findings = [coverage]

    findings = _deduplicate(findings)

    from .optimization_compatibility import apply_plan_gates

    compatibility = apply_plan_gates(
        findings,
        snapshot,
        context.profile,
    )

    status_counts = {
        status.value: sum(
            1 for item in findings if item.status == status
        )
        for status in OptimizationStatus
    }
    summary = {
        "finding_count": len(findings),
        "status_counts": status_counts,
        "apply_recommended_count": status_counts[
            OptimizationStatus.APPLY_RECOMMENDED.value
        ],
        "manual_review_count": status_counts[
            OptimizationStatus.MANUAL_REVIEW.value
        ],
        "blocked_count": status_counts[
            OptimizationStatus.BLOCKED.value
        ],
        "automation_eligible_count": sum(
            1 for item in findings if item.automation_eligible
        ),
        "compatibility": compatibility,
    }

    return OptimizationPlan(
        plan_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        profile=context.profile,
        mode="DRY_RUN",
        execution_ready=False,
        findings=findings,
        summary=summary,
    )
