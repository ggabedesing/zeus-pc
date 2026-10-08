import unittest

from zeus_pc.models import ProbeResult, Status
from zeus_pc.optimization import (
    Confidence,
    OptimizationFinding,
    OptimizationStatus,
)
from zeus_pc.optimization_compatibility import apply_plan_gates


def domains(power_status=Status.OK):
    return [
        ProbeResult("Windows", Status.OK, source="test"),
        ProbeResult("Power", power_status, source="test"),
    ]


def finding(
    finding_id="test.finding",
    action_id="SET_ACTIVE_POWER_PLAN",
    status=OptimizationStatus.APPLY_RECOMMENDED,
    confidence=Confidence.HIGH,
    risk="LOW",
    reversible=True,
    category="POWER",
):
    return OptimizationFinding(
        id=finding_id,
        title="Test",
        category=category,
        status=status,
        reason="Test",
        benefit="PERFORMANCE",
        confidence=confidence,
        risk=risk,
        reversible=reversible,
        action_id=action_id,
    )


class OptimizationCompatibilityTests(unittest.TestCase):
    def test_safe_candidate_is_static_eligible_but_not_executable(self):
        item = finding()

        report = apply_plan_gates(
            [item],
            domains(),
            "GAMING",
        )

        self.assertEqual(report["static_eligible_count"], 1)
        self.assertEqual(report["executable_count"], 0)
        self.assertFalse(report["transaction_engine_ready"])
        self.assertFalse(item.automation_eligible)
        self.assertIn(
            "TRANSACTION_ENGINE_NOT_AVAILABLE",
            item.blockers,
        )
        self.assertEqual(
            report["finding_readiness"][0]["state"],
            "STATIC_ELIGIBLE",
        )

    def test_unknown_action_is_blocked(self):
        item = finding(action_id="UNKNOWN_ACTION")

        report = apply_plan_gates(
            [item],
            domains(),
            "GENERAL",
        )

        self.assertEqual(report["static_eligible_count"], 0)
        self.assertIn("ACTION_NOT_ALLOWLISTED", item.blockers)

    def test_required_domain_must_be_ok(self):
        item = finding()

        report = apply_plan_gates(
            [item],
            domains(power_status=Status.FAILED),
            "GENERAL",
        )

        self.assertEqual(report["static_eligible_count"], 0)
        self.assertIn(
            "REQUIRED_DOMAIN_NOT_OK:Power:FAILED",
            item.blockers,
        )

    def test_confidence_risk_and_reversibility_are_gated(self):
        low_confidence = finding(
            finding_id="low.confidence",
            confidence=Confidence.MEDIUM,
        )
        high_risk = finding(
            finding_id="high.risk",
            risk="HIGH",
        )
        irreversible = finding(
            finding_id="irreversible",
            reversible=False,
        )

        report = apply_plan_gates(
            [low_confidence, high_risk, irreversible],
            domains(),
            "GENERAL",
        )

        self.assertEqual(report["static_eligible_count"], 0)
        self.assertIn(
            "CONFIDENCE_NOT_HIGH",
            low_confidence.blockers,
        )
        self.assertIn(
            "RISK_EXCEEDS_POLICY",
            high_risk.blockers,
        )
        self.assertIn(
            "REVERSIBILITY_REQUIRED",
            irreversible.blockers,
        )

    def test_manual_review_is_never_static_eligible(self):
        item = finding(
            status=OptimizationStatus.MANUAL_REVIEW,
        )

        report = apply_plan_gates(
            [item],
            domains(),
            "GAMING",
        )

        self.assertEqual(report["static_eligible_count"], 0)
        self.assertIn(
            "STATUS_NOT_APPLY_RECOMMENDED",
            item.blockers,
        )
        self.assertEqual(
            report["finding_readiness"][0]["state"],
            "MANUAL_REVIEW",
        )

    def test_distinct_findings_for_same_action_create_collision(self):
        first = finding(finding_id="first")
        second = finding(finding_id="second")

        report = apply_plan_gates(
            [first, second],
            domains(),
            "GENERAL",
        )

        self.assertEqual(report["conflict_count"], 1)
        self.assertEqual(
            report["conflicts"][0]["type"],
            "ACTION_COLLISION",
        )
        self.assertIn(
            "ACTION_COLLISION:SET_ACTIVE_POWER_PLAN",
            first.blockers,
        )
        self.assertIn(
            "ACTION_COLLISION:SET_ACTIVE_POWER_PLAN",
            second.blockers,
        )

    def test_non_actionable_finding_is_classified_without_execution(self):
        item = finding(
            action_id=None,
            status=OptimizationStatus.NO_ACTION,
        )

        report = apply_plan_gates(
            [item],
            domains(),
            "GENERAL",
        )

        self.assertEqual(report["non_actionable_count"], 1)
        self.assertEqual(
            report["finding_readiness"][0]["state"],
            "NON_ACTIONABLE",
        )


if __name__ == "__main__":
    unittest.main()
