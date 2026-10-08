import unittest
from unittest.mock import patch

from zeus_pc.models import Status
from zeus_pc.software_platform import (
    installed_software_probe,
    optional_features_probe,
    virtualization_probe,
)


def metric(result, name):
    return next(item.value for item in result.metrics if item.name == name)


class SoftwarePlatformTests(unittest.TestCase):
    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch("zeus_pc.software_platform.run_json")
    def test_installed_software_deduplicates_and_sorts(self, run_json, _blocked):
        run_json.return_value = [
            {"DisplayName": "Zulu", "DisplayVersion": "1", "Publisher": "A", "Scope": "MACHINE_64"},
            {"DisplayName": "alpha", "DisplayVersion": "2", "Publisher": "B", "Scope": "CURRENT_USER"},
            {"DisplayName": "Zulu", "DisplayVersion": "1", "Publisher": "A", "Scope": "MACHINE_32"},
        ]

        result = installed_software_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "count"), 2)
        items = metric(result, "items")
        self.assertEqual(items[0]["DisplayName"], "alpha")
        self.assertEqual(sorted(items[1]["Scopes"]), ["MACHINE_32", "MACHINE_64"])

    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch("zeus_pc.software_platform.run_json")
    def test_virtualization_probe_preserves_context(self, run_json, _blocked):
        run_json.return_value = {
            "HypervisorPresent": True,
            "VirtualizationFirmwareEnabled": True,
            "SecondLevelAddressTranslationExtensions": True,
            "VirtualizationBasedSecurityStatus": 2,
            "SecurityServicesConfigured": [1, 2],
            "SecurityServicesRunning": [1],
        }

        result = virtualization_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertTrue(metric(result, "hypervisor_present"))
        self.assertTrue(metric(result, "firmware_virtualization_enabled"))
        self.assertEqual(metric(result, "vbs_status"), 2)

    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch("zeus_pc.software_platform.run_json")
    def test_optional_features_counts_enabled(self, run_json, _blocked):
        run_json.return_value = [
            {"FeatureName": "A", "State": "Enabled"},
            {"FeatureName": "B", "State": "Disabled"},
            {"FeatureName": "C", "State": "Enabled"},
        ]

        result = optional_features_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "count"), 3)
        self.assertEqual(metric(result, "enabled_count"), 2)

    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch(
        "zeus_pc.software_platform.run_json",
        side_effect=RuntimeError("Access is denied"),
    )
    def test_optional_features_access_denied_requires_elevation(self, run_json, _blocked):
        result = optional_features_probe()
        self.assertEqual(result.status, Status.REQUIRES_ELEVATION)

    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch(
        "zeus_pc.software_platform.run_json",
        side_effect=RuntimeError("Get-WindowsOptionalFeature is not recognized"),
    )
    def test_missing_optional_feature_cmdlet_is_unsupported(self, run_json, _blocked):
        result = optional_features_probe()
        self.assertEqual(result.status, Status.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
