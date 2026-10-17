import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.agent import build_context, validate_plan
from zeus_pc.agent.context import DEFAULT_TOKEN_LIMIT
from zeus_pc.agent.planner import (
    MAX_PLAN_STEPS,
    PlanError,
    PlanRisk,
    prepare_plan,
)
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import (
    ActionDefinition,
    ActionRegistry,
    TransactionEngine,
    TransactionError,
)


def fake_snapshot():
    return [
        {
            "domain": "CPU",
            "status": "OK",
            "metrics": [{"name": "model", "value": "Ryzen 5 5600"}],
        },
        {
            "domain": "GPU",
            "status": "OK",
            "metrics": [{"name": "adapters", "value": [{"Name": "RX 6600"}]}],
        },
        {
            "domain": "RAM",
            "status": "OK",
            "metrics": [{"name": "installed_gb", "value": 16}],
        },
        {
            "domain": "Windows",
            "status": "OK",
            "metrics": [
                {"name": "edition", "value": "Windows 11 Pro"},
                {"name": "build", "value": 26100},
            ],
        },
        {
            "domain": "Storage",
            "status": "ATTENTION",
            "metrics": [{"name": "volumes", "value": [{"free_gb": 12}]}],
        },
    ]


def fake_plan(findings=None):
    return {
        "plan_id": "plan-1",
        "profile": "GENERAL",
        "findings": findings
        if findings is not None
        else [
            {
                "id": "power.plan.moderate",
                "title": "Usar plano Equilibrado",
                "status": "APPLY_RECOMMENDED",
                "action_id": "SET_ACTIVE_POWER_PLAN",
                "risk": "LOW",
                "confidence": "HIGH",
            },
            {
                "id": "startup.noise",
                "title": "Revisar inicializacao",
                "status": "MANUAL_REVIEW",
                "action_id": None,
                "risk": "LOW",
                "confidence": "MEDIUM",
            },
        ],
    }


def fake_baseline():
    return {
        "sample_count": 42,
        "metrics": {
            "cpu_pct": {"count": 42, "min": 3.0, "avg": 17.5, "max": 61.0},
            "ram_used_pct": {"count": 42, "min": 40.0, "avg": 55.2, "max": 70.0},
        },
    }


class AgentContextTests(unittest.TestCase):
    def build(self, **kwargs):
        options = {
            "profile": "GENERAL",
            "snapshot": fake_snapshot(),
            "optimization_plan": fake_plan(),
            "baseline": fake_baseline(),
            "source_scan_id": 7,
        }
        options.update(kwargs)
        return build_context(**options)

    def test_prompt_contains_hardware_domains_findings_and_baseline(self):
        prompt = self.build().to_prompt()

        self.assertIn("Ryzen 5 5600", prompt)
        self.assertIn("RX 6600", prompt)
        self.assertIn("RAM: 16 GB", prompt)
        self.assertIn("Windows 11 Pro", prompt)
        self.assertIn("Storage: ATTENTION", prompt)
        self.assertIn("Usar plano Equilibrado", prompt)
        self.assertIn("SET_ACTIVE_POWER_PLAN", prompt)
        self.assertIn("cpu_pct", prompt)
        self.assertNotIn("Revisar inicializacao", prompt)

    def test_context_is_deterministic_for_same_inputs(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first.to_prompt(), second.to_prompt())

    def test_budget_truncates_and_reports(self):
        # Limite minimo do construtor e 200 tokens; usa um snapshot grande
        # para garantir corte real dentro do orcamento.
        big_snapshot = [
            {
                "domain": f"Volume_{index}",
                "status": "OK",
                "metrics": [
                    {"name": f"metric_{j}", "value": "x" * 80}
                    for j in range(6)
                ],
            }
            for index in range(30)
        ]
        context = self.build(
            snapshot=big_snapshot,
            token_limit=250,
        )
        self.assertTrue(context.truncated)
        self.assertLessEqual(context.estimated_tokens, 250)
        full = self.build(snapshot=big_snapshot, token_limit=5000)
        self.assertFalse(full.truncated)
        self.assertLess(len(context.to_prompt()), len(full.to_prompt()))

    def test_default_budget_is_not_truncated_for_normal_snapshot(self):
        context = self.build()
        self.assertFalse(context.truncated)
        self.assertLessEqual(context.estimated_tokens, DEFAULT_TOKEN_LIMIT)

    def test_dict_exposes_prompt_and_metadata(self):
        payload = self.build().to_dict()
        self.assertEqual(payload["profile"], "GENERAL")
        self.assertEqual(payload["source_scan_id"], 7)
        self.assertIn("prompt", payload)
        self.assertIsInstance(payload["sections"], list)
        self.assertTrue(payload["sections"])


class AgentPlannerTests(unittest.TestCase):
    def make_registry(self, reversible=True):
        registry = ActionRegistry()
        registry.register(
            ActionDefinition(
                action_id="TEST_ACTION",
                resource_key="TEST_RESOURCE",
                required_privilege="USER",
                reversible=reversible,
                validate_args=lambda args: {
                    "value": int(args["value"]),
                },
                capture_before=lambda args: {"value": 1},
                verify=lambda args: {"ok": True},
                rollback_preview=lambda before: None,
            )
        )
        return registry

    def test_valid_plan_is_normalized_with_risk_and_consent(self):
        plan = validate_plan(
            {
                "explanation": "trocar plano",
                "steps": [
                    {
                        "action_id": "test_action",
                        "args": {"value": "2"},
                        "rationale": "evidencia",
                    }
                ],
            },
            registry=self.make_registry(),
            profile="gaming",
        )

        self.assertEqual(plan.profile, "GAMING")
        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.action_id, "TEST_ACTION")
        self.assertEqual(step.args, {"value": 2})
        self.assertEqual(step.risk, PlanRisk.MEDIUM)
        self.assertTrue(step.consent_required)
        self.assertEqual(plan.max_risk, PlanRisk.MEDIUM)
        self.assertTrue(plan.consent_required)
        payload = plan.to_dict()
        self.assertEqual(payload["max_risk"], "MEDIUM")

    def test_irreversible_action_raises_max_risk(self):
        plan = validate_plan(
            {
                "steps": [
                    {"action_id": "TEST_ACTION", "args": {"value": 2}}
                ]
            },
            registry=self.make_registry(reversible=False),
        )
        self.assertEqual(plan.max_risk, PlanRisk.HIGH)

    def test_rejects_unknown_action_extra_keys_and_bad_shapes(self):
        registry = self.make_registry()
        with self.assertRaises(PlanError):
            validate_plan({"steps": []}, registry=registry)
        with self.assertRaises(PlanError):
            validate_plan("not-a-dict", registry=registry)
        with self.assertRaises(PlanError):
            validate_plan(
                {"steps": [{"action_id": "TEST_ACTION", "args": {"value": 1}}], "foo": 1},
                registry=registry,
            )
        with self.assertRaises(PlanError):
            validate_plan(
                {"steps": [{"action_id": "HACK", "args": {}}]},
                registry=registry,
            )
        with self.assertRaises(PlanError):
            validate_plan(
                {"steps": [{"action_id": "TEST_ACTION", "args": {"value": 1}, "cmd": "rm"}]},
                registry=registry,
            )
        with self.assertRaises(PlanError):
            validate_plan(
                {"steps": [{"action_id": "TEST_ACTION", "args": "shell"}]},
                registry=registry,
            )
        with self.assertRaises(PlanError):
            validate_plan(
                {
                    "steps": [
                        {"action_id": "TEST_ACTION", "args": {"value": "not-int"}}
                    ]
                },
                registry=registry,
            )
        with self.assertRaises(PlanError):
            validate_plan(
                {
                    "steps": [
                        {"action_id": "TEST_ACTION", "args": {"value": 1}}
                    ]
                    * (MAX_PLAN_STEPS + 1)
                },
                registry=registry,
            )

    def test_default_registry_validates_power_plan_guid(self):
        plans = [
            {
                "guid": "381b4222-f694-41f0-9685-ff5bb260df2e",
                "name": "Equilibrado",
                "active": True,
            }
        ]
        with patch(
            "zeus_pc.transaction.list_power_plans",
            return_value=plans,
        ):
            plan = validate_plan(
                {
                    "steps": [
                        {
                            "action_id": "SET_ACTIVE_POWER_PLAN",
                            "args": {
                                "guid": "381B4222-F694-41F0-9685-FF5BB260DF2E"
                            },
                        }
                    ]
                }
            )
            self.assertEqual(
                plan.steps[0].args,
                {"guid": "381b4222-f694-41f0-9685-ff5bb260df2e"},
            )

            with self.assertRaises(PlanError):
                validate_plan(
                    {
                        "steps": [
                            {
                                "action_id": "SET_ACTIVE_POWER_PLAN",
                                "args": {"guid": "not-a-guid"},
                            }
                        ]
                    }
                )

    def test_prepare_plan_journals_prepared_transactions_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            engine = TransactionEngine(db, registry=self.make_registry())
            plan = validate_plan(
                {
                    "steps": [
                        {"action_id": "TEST_ACTION", "args": {"value": 2}}
                    ]
                },
                registry=self.make_registry(),
            )

            prepared = prepare_plan(engine, plan)

            self.assertEqual(len(prepared), 1)
            stored = db.get_transaction(prepared[0].transaction_id)
            self.assertEqual(stored["state"], "PREPARED")
            self.assertIsNone(stored["apply"])


if __name__ == "__main__":
    unittest.main()