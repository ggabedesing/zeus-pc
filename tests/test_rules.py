import unittest

from zeus_pc.engine import build_recommendations
from zeus_pc.models import Metric, ProbeResult, Status
from zeus_pc.rules import make_context


class RuleContextTests(unittest.TestCase):
    def test_unknown_profile_falls_back_to_general(self):
        context = make_context([], "anything")
        self.assertEqual(context.profile, "GENERAL")

    def test_gaming_profile_uses_stricter_startup_triage(self):
        startup = ProbeResult(
            "Startup",
            Status.OK,
            [Metric("items", [{"Name": str(index)} for index in range(8)])],
        )

        gaming = build_recommendations([startup], "GAMING")
        general = build_recommendations([startup], "GENERAL")

        self.assertTrue(any(item.id == "startup.review.v2" for item in gaming))
        self.assertFalse(any(item.id == "startup.review.v2" for item in general))

    def test_gaming_profile_can_request_power_review(self):
        power = ProbeResult(
            "Power",
            Status.OK,
            [Metric("active_scheme", "Equilibrado")],
        )
        result = build_recommendations([power], "GAMING")
        item = next(entry for entry in result if entry.id == "power.profile-alignment.v1")
        self.assertEqual(item.status, "MANUAL_REVIEW")
        self.assertEqual(item.action_id, "SET_ACTIVE_POWER_PLAN")

    def test_high_performance_plan_needs_no_power_review(self):
        power = ProbeResult(
            "Power",
            Status.OK,
            [Metric("active_scheme", "Alto desempenho")],
        )
        result = build_recommendations([power], "GAMING")
        self.assertFalse(any(item.id == "power.profile-alignment.v1" for item in result))

    def test_device_problem_becomes_manual_review(self):
        devices = ProbeResult("Devices", Status.OK, [Metric("problem_count", 2)])
        result = build_recommendations([devices], "GENERAL")
        item = next(entry for entry in result if entry.id == "devices.problem-detected.v1")
        self.assertEqual(item.status, "MANUAL_REVIEW")

    def test_pending_reboot_is_recommended(self):
        integrity = ProbeResult("Integrity", Status.NOT_CHECKED, [Metric("pending_reboot", True)])
        result = build_recommendations([integrity], "GENERAL")
        item = next(entry for entry in result if entry.id == "windows.pending-reboot.v1")
        self.assertEqual(item.status, "RECOMMENDED")


if __name__ == "__main__":
    unittest.main()
