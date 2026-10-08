import unittest
from unittest.mock import patch

from zeus_pc.models import Status
from zeus_pc.storage_health import (
    physical_storage_probe,
    storage_reliability_probe,
    trim_probe,
    windows_event_summary_probe,
)


def metric(result, name):
    return next(item.value for item in result.metrics if item.name == name)


class StorageHealthTests(unittest.TestCase):
    @patch("zeus_pc.storage_health._blocked", return_value=None)
    @patch("zeus_pc.storage_health.run_json")
    def test_physical_storage_counts_unhealthy_disks(self, run_json, _blocked):
        run_json.return_value = [
            {"FriendlyName": "SSD", "HealthStatus": "Healthy"},
            {"FriendlyName": "Disk2", "HealthStatus": "Warning"},
        ]

        result = physical_storage_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "disk_count"), 2)
        self.assertEqual(metric(result, "unhealthy_count"), 1)

    @patch("zeus_pc.storage_health._blocked", return_value=None)
    @patch("zeus_pc.storage_health.run_json")
    def test_reliability_is_partial_when_counters_are_unavailable(self, run_json, _blocked):
        run_json.return_value = [
            {
                "FriendlyName": "SSD",
                "HealthStatus": "Healthy",
                "ReliabilityAvailable": False,
                "ReliabilityError": "not exposed",
            }
        ]

        result = storage_reliability_probe()

        self.assertEqual(result.status, Status.PARTIAL)
        self.assertEqual(metric(result, "disk_count"), 1)
        self.assertEqual(metric(result, "available_count"), 0)

    @patch("zeus_pc.storage_health._blocked", return_value=None)
    @patch("zeus_pc.storage_health.run")
    def test_trim_parses_ntfs_and_refs(self, run, _blocked):
        run.return_value = (
            0,
            "NTFS DisableDeleteNotify = 0\nReFS DisableDeleteNotify = 1",
            "",
        )

        result = trim_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertTrue(metric(result, "ntfs_enabled"))
        self.assertFalse(metric(result, "refs_enabled"))

    @patch("zeus_pc.storage_health._blocked", return_value=None)
    @patch("zeus_pc.storage_health.run_json")
    def test_event_summary_counts_levels_and_providers(self, run_json, _blocked):
        run_json.return_value = [
            {"Level": 1, "ProviderName": "Kernel-Power", "Id": 41},
            {"Level": 2, "ProviderName": "Disk", "Id": 7},
            {"Level": 2, "ProviderName": "Disk", "Id": 51},
        ]

        result = windows_event_summary_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "event_count"), 3)
        self.assertEqual(metric(result, "critical_count"), 1)
        self.assertEqual(metric(result, "error_count"), 2)
        self.assertEqual(metric(result, "top_providers")[0]["provider"], "Disk")

    @patch("zeus_pc.storage_health._blocked", return_value=None)
    @patch(
        "zeus_pc.storage_health.run",
        return_value=(1, "", "Access is denied"),
    )
    def test_trim_access_denied_is_requires_elevation(self, run, _blocked):
        result = trim_probe()
        self.assertEqual(result.status, Status.REQUIRES_ELEVATION)


if __name__ == "__main__":
    unittest.main()
