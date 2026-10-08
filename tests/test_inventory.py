import unittest
from unittest.mock import patch

from zeus_pc.inventory import processes_probe, scheduled_tasks_probe, services_probe
from zeus_pc.models import Status


def metric(result, name):
    return next(item.value for item in result.metrics if item.name == name)


class OperationalInventoryTests(unittest.TestCase):
    @patch("zeus_pc.inventory._blocked", return_value=None)
    @patch("zeus_pc.inventory.run_json")
    def test_services_inventory_counts_running_and_automatic(self, run_json, _blocked):
        run_json.return_value = [
            {"Name": "A", "State": "Running", "StartMode": "Auto"},
            {"Name": "B", "State": "Stopped", "StartMode": "Manual"},
            {"Name": "C", "State": "Running", "StartMode": "Automatic"},
        ]

        result = services_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "count"), 3)
        self.assertEqual(metric(result, "running_count"), 2)
        self.assertEqual(metric(result, "automatic_count"), 2)

    @patch("zeus_pc.inventory._blocked", return_value=None)
    @patch("zeus_pc.inventory.run_json")
    def test_scheduled_tasks_inventory_counts_disabled(self, run_json, _blocked):
        run_json.return_value = [
            {"TaskName": "One", "State": "Ready"},
            {"TaskName": "Two", "State": "Disabled"},
        ]

        result = scheduled_tasks_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "count"), 2)
        self.assertEqual(metric(result, "disabled_count"), 1)

    @patch("zeus_pc.inventory._blocked", return_value=None)
    @patch("zeus_pc.inventory.run_json")
    def test_processes_inventory_aggregates_and_orders_memory(self, run_json, _blocked):
        run_json.return_value = [
            {"Name": "small.exe", "WorkingSetSize": "100"},
            {"Name": "large.exe", "WorkingSetSize": 900},
            {"Name": "middle.exe", "WorkingSetSize": 400},
        ]

        result = processes_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "count"), 3)
        self.assertEqual(metric(result, "total_working_set_bytes"), 1400)
        self.assertEqual(metric(result, "top_memory")[0]["Name"], "large.exe")

    @patch("zeus_pc.inventory._blocked", return_value=None)
    @patch("zeus_pc.inventory.run_json", side_effect=RuntimeError("query failed"))
    def test_domain_failure_is_explicit(self, run_json, _blocked):
        result = services_probe()

        self.assertEqual(result.status, Status.FAILED)
        self.assertIn("query failed", result.message)


if __name__ == "__main__":
    unittest.main()
