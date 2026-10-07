from __future__ import annotations

from .models import ProbeResult, Recommendation


def metric(result: ProbeResult | None, name: str):
    if not result:
        return None
    for item in result.metrics:
        if item.name == name:
            return item.value
    return None


def build_recommendations(snapshot: list[ProbeResult], profile: str = "GENERAL") -> list[Recommendation]:
    domains = {item.domain: item for item in snapshot}
    recommendations: list[Recommendation] = []

    for volume in metric(domains.get("Storage"), "volumes") or []:
        if str(volume.get("root", "")).upper().startswith("C:") and float(volume.get("free_pct", 100)) < 15:
            recommendations.append(Recommendation(
                "storage.low-space", "Libere espaco no disco do sistema", "STORAGE", "RECOMMENDED",
                f"O volume C: possui apenas {volume.get('free_pct')}% de espaco livre.", "SPACE_RECOVERY", "LOW", False
            ))

    usable = metric(domains.get("RAM"), "usable_gb")
    available = metric(domains.get("RAM"), "available_gb")
    if usable and available is not None and available / usable < 0.15:
        recommendations.append(Recommendation(
            "memory.pressure", "Pressao de memoria detectada", "PERFORMANCE", "MANUAL_REVIEW",
            f"Ha somente {available:.1f} GB disponiveis de {usable:.1f} GB utilizaveis.", "STABILITY"
        ))

    pending = metric(domains.get("Windows Update"), "pending_count")
    if isinstance(pending, int) and pending > 0:
        recommendations.append(Recommendation(
            "windows.pending-updates", "Atualizacoes do Windows disponiveis", "WINDOWS", "RECOMMENDED",
            f"O Windows Update encontrou {pending} atualizacao(oes) pendente(s).", "STABILITY_SECURITY", "LOW", False
        ))

    startup_items = metric(domains.get("Startup"), "items") or []
    if len(startup_items) > 8:
        recommendations.append(Recommendation(
            "startup.review", "Revise programas que iniciam com o Windows", "STARTUP", "MANUAL_REVIEW",
            f"Foram encontrados {len(startup_items)} itens de inicializacao.", "BOOT_RESOURCE_REDUCTION"
        ))

    if not recommendations:
        recommendations.append(Recommendation(
            "system.no-action", "Nenhuma alteracao importante e necessaria agora", "SYSTEM", "ALREADY_CORRECT",
            "O diagnostico atual nao encontrou uma mudanca claramente necessaria.", "NONE"
        ))
    return recommendations
