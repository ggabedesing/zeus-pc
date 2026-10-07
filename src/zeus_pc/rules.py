from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ProbeResult, Recommendation

VALID_PROFILES = {
    "GENERAL",
    "GAMING",
    "GAMING_STREAMING",
    "WORK",
    "EDITING",
    "PROGRAMMING",
}


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
    normalized = (profile or "GENERAL").upper().strip()
    if normalized not in VALID_PROFILES:
        normalized = "GENERAL"
    return RuleContext({item.domain: item for item in snapshot}, normalized)


def evaluate_rules(context: RuleContext) -> list[Recommendation]:
    recommendations: list[Recommendation] = []

    for volume in context.metric("Storage", "volumes", []) or []:
        root = str(volume.get("root", "")).upper()
        if root.startswith("C:") and float(volume.get("free_pct", 100)) < 15:
            recommendations.append(Recommendation(
                id="storage.low-space",
                title="Libere espaço no disco do sistema",
                category="STORAGE",
                status="RECOMMENDED",
                reason=f"O volume C: possui apenas {volume.get('free_pct')}% de espaço livre.",
                benefit="SPACE_RECOVERY",
                risk="LOW",
                reversible=False,
            ))

    usable = context.metric("RAM", "usable_gb")
    available = context.metric("RAM", "available_gb")
    if usable and available is not None and float(available) / float(usable) < 0.15:
        recommendations.append(Recommendation(
            id="memory.pressure.v1",
            title="Pressão de memória detectada",
            category="PERFORMANCE",
            status="MANUAL_REVIEW",
            reason=f"Há somente {float(available):.1f} GB disponíveis de {float(usable):.1f} GB utilizáveis.",
            benefit="STABILITY",
            risk="LOW",
        ))

    problem_devices = context.metric("Devices", "problem_count", 0) or 0
    if int(problem_devices) > 0:
        recommendations.append(Recommendation(
            id="devices.problem-detected.v1",
            title="Dispositivos com erro foram detectados",
            category="DRIVERS",
            status="MANUAL_REVIEW",
            reason=f"O Windows reportou {int(problem_devices)} dispositivo(s) com código de problema.",
            benefit="STABILITY",
            risk="LOW",
        ))

    unsigned_drivers = context.metric("Drivers", "unsigned_count", 0) or 0
    if int(unsigned_drivers) > 0:
        recommendations.append(Recommendation(
            id="drivers.unsigned-review.v1",
            title="Revise drivers não assinados",
            category="DRIVERS",
            status="MANUAL_REVIEW",
            reason=f"Foram encontrados {int(unsigned_drivers)} driver(s) sem assinatura confirmada pelo inventário atual.",
            benefit="STABILITY_SECURITY",
            risk="LOW",
        ))

    pending_reboot = context.metric("Integrity", "pending_reboot", False)
    if pending_reboot is True:
        recommendations.append(Recommendation(
            id="windows.pending-reboot.v1",
            title="O Windows precisa concluir uma reinicialização",
            category="WINDOWS",
            status="RECOMMENDED",
            reason="O estado de servicing indica uma reinicialização pendente.",
            benefit="STABILITY",
            risk="LOW",
            reversible=False,
        ))

    pending = context.metric("Windows Update", "pending_count")
    if isinstance(pending, int) and pending > 0:
        recommendations.append(Recommendation(
            id="windows.pending-updates.v1",
            title="Atualizações do Windows disponíveis",
            category="WINDOWS",
            status="RECOMMENDED",
            reason=f"O Windows Update encontrou {pending} atualização(ões) pendente(s).",
            benefit="STABILITY_SECURITY",
            risk="LOW",
            reversible=False,
        ))

    startup_items = context.metric("Startup", "items", []) or []
    startup_threshold = 6 if context.profile in {"GAMING", "GAMING_STREAMING"} else 10
    if len(startup_items) > startup_threshold:
        recommendations.append(Recommendation(
            id="startup.review.v2",
            title="Revise programas que iniciam com o Windows",
            category="STARTUP",
            status="MANUAL_REVIEW",
            reason=(
                f"Foram encontrados {len(startup_items)} itens de inicialização. "
                f"O perfil {context.profile} usa um limite de triagem de {startup_threshold} itens."
            ),
            benefit="BOOT_RESOURCE_REDUCTION",
            risk="LOW",
        ))

    active_scheme = str(context.metric("Power", "active_scheme", "") or "")
    if context.profile in {"GAMING", "GAMING_STREAMING"}:
        normalized = active_scheme.lower()
        if active_scheme and not any(token in normalized for token in ("alto desempenho", "high performance", "desempenho máximo", "ultimate performance")):
            recommendations.append(Recommendation(
                id="power.profile-alignment.v1",
                title="Revise o plano de energia para este perfil",
                category="POWER",
                status="MANUAL_REVIEW",
                reason=(
                    f"O perfil {context.profile} está ativo, mas o plano atual não foi reconhecido "
                    "como um plano orientado a desempenho."
                ),
                benefit="ENERGY_POLICY_ALIGNMENT",
                risk="LOW",
                reversible=True,
                action_id="SET_ACTIVE_POWER_PLAN",
            ))

    if not recommendations:
        recommendations.append(Recommendation(
            id="system.no-action.v1",
            title="Nenhuma alteração importante é necessária agora",
            category="SYSTEM",
            status="ALREADY_CORRECT",
            reason="O diagnóstico atual não encontrou uma mudança claramente necessária para este perfil.",
            benefit="NONE",
            risk="LOW",
        ))

    return recommendations
