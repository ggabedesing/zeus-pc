import unittest
from unittest.mock import patch

from zeus_pc.models import Status
from zeus_pc.platform_inventory import (
    firewall_probe,
    network_link_probe,
    proxy_probe,
    tpm_probe,
)


def metric(result, name):
    return next(item.value for item in result.metrics if item.name == name)


class PlatformInventoryTests(unittest.TestCase):
    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_tpm_probe_preserves_security_state(self, run_json, _blocked):
        run_json.return_value = {
            "TpmPresent": True,
            "TpmReady": True,
            "TpmEnabled": True,
            "TpmActivated": True,
            "AutoProvisioning": "Enabled",
        }

        result = tpm_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertTrue(metric(result, "present"))
        self.assertTrue(metric(result, "ready"))
        self.assertTrue(metric(result, "enabled"))

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_firewall_profiles_aggregate_enabled_state(self, run_json, _blocked):
        run_json.return_value = [
            {"Name": "Domain", "Enabled": True},
            {"Name": "Private", "Enabled": True},
            {"Name": "Public", "Enabled": False},
        ]

        result = firewall_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "profile_count"), 3)
        self.assertEqual(metric(result, "enabled_count"), 2)
        self.assertFalse(metric(result, "all_profiles_enabled"))

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_proxy_probe_normalizes_integer_enable_flag(self, run_json, _blocked):
        run_json.return_value = {
            "ProxyEnable": 1,
            "ProxyServer": "127.0.0.1:8080",
            "AutoConfigURL": None,
            "AutoDetect": True,
        }

        result = proxy_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertTrue(metric(result, "enabled"))
        self.assertEqual(metric(result, "scope"), "CURRENT_USER")

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_network_link_counts_up_adapters(self, run_json, _blocked):
        run_json.return_value = [
            {"Name": "Ethernet", "Status": "Up", "LinkSpeed": "1 Gbps"},
            {"Name": "Wi-Fi", "Status": "Disconnected"},
        ]

        result = network_link_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "adapter_count"), 2)
        self.assertEqual(metric(result, "up_count"), 1)

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch(
        "zeus_pc.platform_inventory.run_json",
        side_effect=RuntimeError("Access is denied"),
    )
    def test_access_denied_maps_to_requires_elevation(self, run_json, _blocked):
        result = tpm_probe()
        self.assertEqual(result.status, Status.REQUIRES_ELEVATION)

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch(
        "zeus_pc.platform_inventory.run_json",
        side_effect=RuntimeError("Get-Tpm is not recognized"),
    )
    def test_missing_cmdlet_maps_to_unsupported(self, run_json, _blocked):
        result = tpm_probe()
        self.assertEqual(result.status, Status.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
