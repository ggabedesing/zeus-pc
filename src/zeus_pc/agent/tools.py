"""Tool catalog for the Zeus Agent (increment 016).

A ToolSpec is the declarative contract a local model (or an external
harness such as Codex/Obsidian via MCP) needs in order to call Zeus:

- read tools map to read-only endpoints and carry a `binding` name;
- mutation tools are generated from the semantic ActionRegistry, keep the
  action's JSON Schema as their input schema and always declare risk and
  consent requirements.

The catalog is deterministic: no clock, no randomness, sorted names.
Nothing here executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..transaction import ActionRegistry, DEFAULT_ACTION_REGISTRY
from .planner import PlanRisk, risk_for_action

CATALOG_VERSION = "1"


class ToolCatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolParameter:
    name: str
    type: str
    description: str
    required: bool = True
    pattern: str | None = None
    enum: tuple[str, ...] | None = None
    default: Any = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.pattern:
            schema["pattern"] = self.pattern
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    title: str
    description: str
    kind: str  # "read" | "mutation"
    binding: str | None = None
    action_id: str | None = None
    parameters: tuple[ToolParameter, ...] = ()
    object_schema: dict[str, Any] | None = None
    risk: PlanRisk | None = None
    consent_required: bool = False
    returns: str = ""

    def input_schema(self) -> dict[str, Any]:
        if self.object_schema is not None:
            return dict(self.object_schema)
        properties = {
            param.name: param.to_schema() for param in self.parameters
        }
        required = [param.name for param in self.parameters if param.required]
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        schema["additionalProperties"] = False
        return schema

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "binding": self.binding,
            "action_id": self.action_id,
            "risk": self.risk.value if self.risk is not None else None,
            "consent_required": self.consent_required,
            "input_schema": self.input_schema(),
            "returns": self.returns,
        }


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: tuple[ToolSpec, ...]
    version: str = CATALOG_VERSION
    _names: tuple[str, ...] = field(default=(), repr=False)

    def names(self) -> list[str]:
        return list(self._names)

    def get(self, name: str) -> ToolSpec | None:
        wanted = str(name or "")
        for tool in self.tools:
            if tool.name == wanted:
                return tool
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "count": len(self.tools),
            "tools": [tool.to_dict() for tool in self.tools],
        }


_READ_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="zeus_status",
        title="Estado do Zeus",
        description=(
            "Versao, modo de execucao, acoes habilitadas, schema do "
            "banco e estado do observer."
        ),
        kind="read",
        binding="status",
        parameters=(),
        returns="object com versao, execution_mode, enabled_actions e persistence",
    ),
    ToolSpec(
        name="zeus_scan",
        title="Analise completa do PC",
        description=(
            "Roda uma analise de leitura (hardware, Windows, energia, "
            "seguranca, inicializacao, armazenamento) e retorna snapshot, "
            "recomendacoes e plano de otimizacao."
        ),
        kind="read",
        binding="scan",
        parameters=(
            ToolParameter(
                name="profile",
                type="string",
                description="Perfil de uso.",
                required=False,
                enum=("GENERAL", "GAMING", "STREAMING"),
            ),
        ),
        returns="object com scan_id, snapshot, recommendations e optimization_plan",
    ),
    ToolSpec(
        name="zeus_context",
        title="Contexto do agente",
        description=(
            "Digest deterministico orçado em tokens do ultimo scan, "
            "das oportunidades e da baseline do observer."
        ),
        kind="read",
        binding="agent_context",
        parameters=(),
        returns="object com sections, estimated_tokens, truncated e prompt",
    ),
    ToolSpec(
        name="zeus_history",
        title="Historico de analises",
        description="Ultimas analises persistidas com snapshot e recomendacoes.",
        kind="read",
        binding="history",
        parameters=(
            ToolParameter(
                name="limit",
                type="integer",
                description="Quantidade de analises (1 a 100).",
                required=False,
                default=10,
            ),
        ),
        returns="object com scans",
    ),
    ToolSpec(
        name="zeus_power_plans",
        title="Planos de energia",
        description="Lista os planos de energia do Windows e marca o ativo.",
        kind="read",
        binding="power_plans",
        parameters=(),
        returns="object com plans",
    ),
    ToolSpec(
        name="zeus_observer_baseline",
        title="Baseline do observer",
        description=(
            "Min/medio/max de CPU, RAM, GPU e temperatura da GPU a "
            "partir das amostras recentes."
        ),
        kind="read",
        binding="observer_baseline",
        parameters=(
            ToolParameter(
                name="limit",
                type="integer",
                description="Quantidade de amostras (1 a 300).",
                required=False,
                default=60,
            ),
        ),
        returns="object com sample_count, duration_seconds e metrics",
    ),
)


def _mutation_tool_name(action_id: str) -> str:
    return action_id.strip().lower()


def build_tool_catalog(
    registry: ActionRegistry = DEFAULT_ACTION_REGISTRY,
    *,
    include_reads: bool = True,
) -> ToolCatalog:
    """Build the deterministic tool catalog from reads + ActionRegistry."""
    tools: list[ToolSpec] = list(_READ_TOOLS) if include_reads else []

    for action_id in registry.ids():
        definition = registry.get(action_id)
        if definition is None:
            continue
        if not isinstance(definition.arg_schema, dict):
            # Sem schema declarativo nao ha contrato publicavel.
            continue
        tools.append(
            ToolSpec(
                name=_mutation_tool_name(action_id),
                title=action_id.replace("_", " ").title(),
                description=(
                    f"Executa {action_id} pelo Transaction Engine: prepare, "
                    "consentimento unico, APPLY, VERIFY independente e "
                    "rollback automatico se o VERIFY falhar."
                ),
                kind="mutation",
                action_id=action_id,
                object_schema=dict(definition.arg_schema),
                risk=risk_for_action(action_id, registry),
                consent_required=True,
                returns=(
                    "object com transaction, state, ok e message "
                    "(VERIFIED, ROLLED_BACK ou FAILED)"
                ),
            )
        )

    names = [tool.name for tool in tools]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise ToolCatalogError(f"nomes de tool duplicados: {duplicated}")

    order = sorted(tools, key=lambda tool: (tool.kind, tool.name))
    return ToolCatalog(
        tools=tuple(order),
        _names=tuple(tool.name for tool in order),
    )
