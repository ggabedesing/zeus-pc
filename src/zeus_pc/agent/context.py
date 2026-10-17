"""Agent context assembly for increment 015.

The context is a deterministic, bounded digest of what Zeus already knows:
hardware inventory, domain diagnostics, optimization findings and the
observer baseline. It is the payload a local LLM would receive before
planning; no network, no third-party service and no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_TOKEN_LIMIT = 3000
_TOKENS_PER_CHAR = 4
_MAX_METRIC_CHARS = 120
_MAX_METRICS_PER_DOMAIN = 6


def _estimate_tokens(text: str) -> int:
    return len(text) // _TOKENS_PER_CHAR


def _shorten(value: Any, limit: int = _MAX_METRIC_CHARS) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _metric_value(domain_item: dict[str, Any], name: str) -> Any:
    for metric in domain_item.get("metrics") or []:
        if isinstance(metric, dict) and metric.get("name") == name:
            return metric.get("value")
    return None


def _first_domain(
    snapshot: list[dict[str, Any]],
    name: str,
) -> dict[str, Any] | None:
    for item in snapshot:
        if isinstance(item, dict) and item.get("domain") == name:
            return item
    return None


def _hardware_section(
    snapshot: list[dict[str, Any]],
) -> str:
    cpu = _first_domain(snapshot, "CPU")
    gpu = _first_domain(snapshot, "GPU")
    ram = _first_domain(snapshot, "RAM")
    windows = _first_domain(snapshot, "Windows")

    lines = []
    if cpu is not None:
        lines.append(f"CPU: {_shorten(_metric_value(cpu, 'model') or 'desconhecida')}")
    if gpu is not None:
        adapters = _metric_value(gpu, "adapters") or []
        name = _metric_value(gpu, "name")
        if not name and isinstance(adapters, list) and adapters:
            first = adapters[0]
            if isinstance(first, dict):
                name = first.get("Name")
        lines.append(f"GPU: {_shorten(name or 'desconhecida')}")
    if ram is not None:
        installed = _metric_value(ram, "installed_gb")
        lines.append(f"RAM: {installed if installed is not None else '?'} GB")
    if windows is not None:
        edition = _metric_value(windows, "edition") or "Windows"
        build = _metric_value(windows, "build")
        suffix = f" (build {build})" if build is not None else ""
        lines.append(f"SO: {_shorten(edition)}{suffix}")
    return "\n".join(lines)


def _domains_section(snapshot: list[dict[str, Any]]) -> str:
    lines = []
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "UNKNOWN")
        status = str(item.get("status") or "UNKNOWN")
        lines.append(f"- {domain}: {status}")
        metrics = [
            metric
            for metric in item.get("metrics") or []
            if isinstance(metric, dict) and metric.get("name")
        ][:_MAX_METRICS_PER_DOMAIN]
        for metric in metrics:
            lines.append(
                f"    {metric['name']} = {_shorten(metric.get('value'))}"
            )
    return "\n".join(lines)


def _findings_section(
    optimization_plan: dict[str, Any] | None,
) -> str:
    if not optimization_plan:
        return ""
    lines = []
    for finding in optimization_plan.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if finding.get("status") != "APPLY_RECOMMENDED":
            continue
        action = finding.get("action_id") or "sem acao"
        lines.append(
            "- {title} | acao={action} | risco={risk} | confianca={confidence}".format(
                title=_shorten(finding.get("title") or finding.get("id") or "?"),
                action=action,
                risk=finding.get("risk") or "?",
                confidence=finding.get("confidence") or "?",
            )
        )
    return "\n".join(lines)


def _baseline_section(baseline: dict[str, Any] | None) -> str:
    if not baseline or not baseline.get("sample_count"):
        return ""
    lines = [f"amostras: {baseline.get('sample_count')}"]
    metrics = baseline.get("metrics") or {}
    for name in sorted(metrics):
        summary = metrics.get(name)
        if isinstance(summary, dict):
            lines.append(
                f"{name}: min={summary.get('min')} avg={summary.get('avg')} "
                f"max={summary.get('max')}"
            )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ContextSection:
    name: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "content": self.content}


@dataclass(frozen=True, slots=True)
class AgentContext:
    profile: str
    source_scan_id: int | None
    sections: tuple[ContextSection, ...]
    estimated_tokens: int
    token_limit: int
    truncated: bool

    def to_prompt(self) -> str:
        blocks = [
            f"# Zeus PC — contexto do agente\nperfil: {self.profile}",
        ]
        for section in self.sections:
            blocks.append(f"## {section.name}\n{section.content}")
        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "source_scan_id": self.source_scan_id,
            "sections": [section.to_dict() for section in self.sections],
            "estimated_tokens": self.estimated_tokens,
            "token_limit": self.token_limit,
            "truncated": self.truncated,
            "prompt": self.to_prompt(),
        }


def build_context(
    *,
    profile: str,
    snapshot: list[dict[str, Any]],
    optimization_plan: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    source_scan_id: int | None = None,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
) -> AgentContext:
    """Assemble a deterministic, token-bounded context from known state."""
    limit = max(int(token_limit), 200)
    candidates: list[tuple[str, str]] = [
        ("hardware", _hardware_section(snapshot)),
        ("diagnostico", _domains_section(snapshot)),
        ("oportunidades", _findings_section(optimization_plan)),
        ("observacao", _baseline_section(baseline)),
    ]

    sections: list[ContextSection] = []
    used_tokens = 0
    truncated = False
    for name, content in candidates:
        if not content:
            continue
        cost = _estimate_tokens(content) + 2
        remaining = limit - used_tokens
        if cost <= remaining:
            sections.append(ContextSection(name, content))
            used_tokens += cost
            continue
        # Corta a seção para caber no orçamento restante.
        char_budget = max(remaining - 2, 0) * _TOKENS_PER_CHAR
        if char_budget <= 0:
            truncated = True
            break
        sections.append(
            ContextSection(name, content[:char_budget])
        )
        used_tokens = limit
        truncated = True
        break

    return AgentContext(
        profile=str(profile or "GENERAL").upper(),
        source_scan_id=source_scan_id,
        sections=tuple(sections),
        estimated_tokens=used_tokens,
        token_limit=limit,
        truncated=truncated,
    )
