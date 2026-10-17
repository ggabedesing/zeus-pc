import unittest

from zeus_pc.agent import build_tool_catalog
from zeus_pc.agent.tools import (
    CATALOG_VERSION,
    ToolCatalogError,
    ToolParameter,
    ToolSpec,
)
from zeus_pc.agent.planner import PlanRisk
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    DEFAULT_ACTION_REGISTRY,
)


def registry_with_schema():
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            action_id="SET_THING",
            resource_key="THING",
            required_privilege="USER",
            reversible=True,
            validate_args=lambda args: dict(args),
            capture_before=lambda args: {},
            verify=lambda args: {"ok": True},
            rollback_preview=lambda before: None,
            arg_schema={
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "required": ["mode"],
                "additionalProperties": False,
            },
        )
    )
    return registry


def registry_without_schema():
    registry = ActionRegistry()
    registry.register(
        ActionDefinition(
            action_id="HIDDEN_ACTION",
            resource_key="OTHER",
            required_privilege="ADMIN",
            reversible=False,
            validate_args=lambda args: dict(args),
            capture_before=lambda args: {},
            verify=lambda args: {"ok": True},
            rollback_preview=lambda before: None,
        )
    )
    return registry


class ToolCatalogTests(unittest.TestCase):
    def test_catalog_contains_reads_and_registry_mutations(self):
        catalog = build_tool_catalog(registry_with_schema())
        names = catalog.names()

        self.assertIn("zeus_status", names)
        self.assertIn("zeus_context", names)
        self.assertIn("set_thing", names)
        self.assertEqual(catalog.version, CATALOG_VERSION)

        tool = catalog.get("set_thing")
        self.assertEqual(tool.kind, "mutation")
        self.assertEqual(tool.action_id, "SET_THING")
        self.assertTrue(tool.consent_required)
        self.assertEqual(tool.risk, PlanRisk.MEDIUM)
        self.assertIn("consentimento unico", tool.description)

    def test_mutation_input_schema_is_strict_json_schema(self):
        catalog = build_tool_catalog(registry_with_schema())
        payload = catalog.get("set_thing").to_dict()

        schema = payload["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["mode"])
        self.assertEqual(
            set(schema["properties"]), {"mode"}
        )

    def test_power_plan_tool_exposes_guid_pattern(self):
        catalog = build_tool_catalog(DEFAULT_ACTION_REGISTRY)
        tool = catalog.get("set_active_power_plan")

        self.assertIsNotNone(tool)
        schema = tool.input_schema()
        guid = schema["properties"]["guid"]
        self.assertEqual(guid["type"], "string")
        self.assertIn("[0-9a-fA-F]{8}", guid["pattern"])
        self.assertTrue(guid["pattern"].startswith("^"))
        self.assertTrue(guid["pattern"].endswith("$"))
        self.assertEqual(schema["required"], ["guid"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(tool.risk, PlanRisk.MEDIUM)
        self.assertTrue(tool.consent_required)

    def test_action_without_arg_schema_is_not_published(self):
        catalog = build_tool_catalog(registry_without_schema())
        self.assertNotIn("hidden_action", catalog.names())
        # Reads still available when include_reads is on.
        self.assertIn("zeus_status", catalog.names())

        only_reads = build_tool_catalog(
            registry_without_schema(),
            include_reads=False,
        )
        self.assertEqual(only_reads.names(), [])

    def test_catalog_is_deterministic_and_sorted(self):
        first = build_tool_catalog().to_dict()
        second = build_tool_catalog().to_dict()
        self.assertEqual(first, second)

        names = build_tool_catalog().names()
        self.assertEqual(names, sorted(names))
        self.assertEqual(first["count"], len(first["tools"]))

    def test_duplicate_tool_names_are_rejected(self):
        # A acao "ZEUS_STATUS" geraria a tool "zeus_status", colidindo
        # com a tool de leitura homonima: o catalogo deve falhar.
        collision = ActionRegistry()
        collision.register(
            ActionDefinition(
                action_id="ZEUS_STATUS",
                resource_key="X",
                required_privilege="USER",
                reversible=True,
                validate_args=lambda args: dict(args),
                capture_before=lambda args: {},
                verify=lambda args: {"ok": True},
                rollback_preview=lambda before: None,
                arg_schema={"type": "object", "properties": {}},
            )
        )
        with self.assertRaises(ToolCatalogError):
            build_tool_catalog(collision)

    def test_read_tool_parameters_generate_schema(self):
        spec = ToolSpec(
            name="demo",
            title="Demo",
            description="d",
            kind="read",
            parameters=(
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Quantidade.",
                    required=False,
                    default=10,
                ),
                ToolParameter(
                    name="profile",
                    type="string",
                    description="Perfil.",
                    enum=("GENERAL", "GAMING"),
                ),
            ),
        )
        schema = spec.input_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["profile"])
        self.assertEqual(
            schema["properties"]["limit"]["default"], 10
        )
        self.assertEqual(
            schema["properties"]["profile"]["enum"],
            ["GENERAL", "GAMING"],
        )
        self.assertFalse(schema["additionalProperties"])

    def test_high_risk_when_privilege_is_not_user(self):
        registry = ActionRegistry()
        registry.register(
            ActionDefinition(
                action_id="ADMIN_ACTION",
                resource_key="ADMIN_RESOURCE",
                required_privilege="ADMIN",
                reversible=True,
                validate_args=lambda args: dict(args),
                capture_before=lambda args: {},
                verify=lambda args: {"ok": True},
                rollback_preview=lambda before: None,
                arg_schema={"type": "object", "properties": {}},
            )
        )
        catalog = build_tool_catalog(registry, include_reads=False)
        tool = catalog.get("admin_action")
        self.assertEqual(tool.risk, PlanRisk.HIGH)
        self.assertTrue(tool.consent_required)


if __name__ == "__main__":
    unittest.main()
