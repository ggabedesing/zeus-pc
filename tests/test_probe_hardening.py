import unittest
from unittest.mock import patch

from zeus_pc.models import Metric, ProbeResult, Status, demote_if_unknown, humanize_probe_error
from zeus_pc.platform_inventory import firewall_probe, proxy_probe, tpm_probe
from zeus_pc.probes import devices_probe, update_probe
from zeus_pc.software_platform import optional_features_probe


def metric(result, name):
    return next(item.value for item in result.metrics if item.name == name)


class HumanizeProbeErrorTests(unittest.TestCase):
    def test_elevation_accented_maps_to_requires_elevation(self):
        status, message = humanize_probe_error(
            RuntimeError("A opera\u00e7\u00e3o solicitada requer eleva\u00e7\u00e3o."),
            "Get-WindowsOptionalFeature",
        )
        self.assertEqual(status, Status.REQUIRES_ELEVATION)
        self.assertEqual(message, "A leitura exige administrador.")

    def test_elevation_english_maps_to_requires_elevation(self):
        status, message = humanize_probe_error(RuntimeError("Access is denied"), "TPM")
        self.assertEqual(status, Status.REQUIRES_ELEVATION)

    def test_timeout_maps_to_partial(self):
        status, message = humanize_probe_error(
            RuntimeError("Tempo esgotado ao executar a consulta."),
            "Windows Update",
        )
        self.assertEqual(status, Status.PARTIAL)
        self.assertEqual(message, "Tempo esgotado ao consultar Windows Update.")

    def test_unsupported_maps_to_unsupported(self):
        status, _ = humanize_probe_error(RuntimeError("Get-Tpm is not recognized"), "TPM")
        self.assertEqual(status, Status.UNSUPPORTED)

    def test_generic_keeps_default_status(self):
        status, message = humanize_probe_error(RuntimeError("boom"), "Win32_Processor")
        self.assertEqual(status, Status.FAILED)
        self.assertEqual(message, "Nao foi possivel ler Win32_Processor.")


class DemoteIfUnknownTests(unittest.TestCase):
    def test_real_zero_is_known(self):
        result = ProbeResult("Devices", Status.OK, [Metric("problem_count", 0)])
        kept = demote_if_unknown(result, {"problem_count"})
        self.assertEqual(kept.status, Status.OK)

    def test_empty_list_is_known(self):
        result = ProbeResult("Devices", Status.OK, [Metric("problems", [])])
        kept = demote_if_unknown(result, {"problems"})
        self.assertEqual(kept.status, Status.OK)

    def test_all_none_demotes_to_partial(self):
        result = ProbeResult("TPM", Status.OK, [Metric("present", None), Metric("ready", None)])
        kept = demote_if_unknown(result, {"present", "ready"})
        self.assertEqual(kept.status, Status.PARTIAL)
        self.assertTrue(kept.message)

    def test_non_ok_result_is_left_alone(self):
        result = ProbeResult("TPM", Status.REQUIRES_ELEVATION, [Metric("present", None)])
        kept = demote_if_unknown(result, {"present"})
        self.assertEqual(kept.status, Status.REQUIRES_ELEVATION)


class ElevationDefectTests(unittest.TestCase):
    @patch("zeus_pc.software_platform._blocked", return_value=None)
    @patch(
        "zeus_pc.software_platform.run_json",
        side_effect=RuntimeError("Get-WindowsOptionalFeature : A opera\u00e7\u00e3o solicitada requer eleva\u00e7\u00e3o."),
    )
    def test_windows_features_reports_requires_elevation(self, run_json, _blocked):
        result = optional_features_probe()

        self.assertEqual(result.status, Status.REQUIRES_ELEVATION)
        self.assertEqual(result.message, "A leitura exige administrador.")

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json", side_effect=RuntimeError("Access is denied"))
    def test_tpm_access_denied_is_not_failed(self, run_json, _blocked):
        result = tpm_probe()
        self.assertEqual(result.status, Status.REQUIRES_ELEVATION)
        self.assertEqual(result.message, "A leitura exige administrador.")


class UnknownMetricsDefectTests(unittest.TestCase):
    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_tpm_all_null_does_not_claim_ok(self, run_json, _blocked):
        run_json.return_value = {
            "TpmPresent": None,
            "TpmReady": None,
            "TpmEnabled": None,
            "TpmActivated": None,
            "ManagedAuthLevel": None,
            "OwnerClearDisabled": None,
            "AutoProvisioning": None,
        }

        result = tpm_probe()

        self.assertNotEqual(result.status, Status.OK)
        self.assertEqual(result.status, Status.PARTIAL)
        self.assertTrue(result.message)

    @patch("zeus_pc.probes._windows_only", return_value=None)
    @patch("zeus_pc.probes.run_json")
    def test_devices_zero_problems_stays_ok(self, run_json, _only):
        run_json.return_value = []

        result = devices_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "problem_count"), 0)

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_proxy_disabled_read_stays_ok(self, run_json, _blocked):
        run_json.return_value = {"ProxyEnable": 0}

        result = proxy_probe()

        self.assertEqual(result.status, Status.OK)
        self.assertFalse(metric(result, "enabled"))

    @patch("zeus_pc.platform_inventory._blocked", return_value=None)
    @patch("zeus_pc.platform_inventory.run_json")
    def test_firewall_with_profiles_stays_ok(self, run_json, _blocked):
        run_json.return_value = [{"Name": "Domain", "Enabled": True}]

        result = firewall_probe()

        self.assertEqual(result.status, Status.OK)


class WindowsUpdateDefectTests(unittest.TestCase):
    @patch("zeus_pc.probes._windows_only", return_value=None)
    @patch("zeus_pc.probes.run_json")
    def test_update_probe_uses_longer_timeout(self, run_json, _only):
        run_json.return_value = {"Pending": 0}

        result = update_probe()

        self.assertEqual(run_json.call_args.kwargs["timeout"], 25)
        self.assertEqual(result.status, Status.OK)
        self.assertEqual(metric(result, "pending_count"), 0)

    @patch("zeus_pc.probes._windows_only", return_value=None)
    @patch(
        "zeus_pc.probes.run_json",
        side_effect=RuntimeError("Tempo esgotado ao executar a consulta."),
    )
    def test_update_timeout_is_clean_partial(self, run_json, _only):
        result = update_probe()

        self.assertEqual(result.status, Status.PARTIAL)
        self.assertEqual(result.message, "Tempo esgotado ao consultar Windows Update.")
        self.assertNotIn("powershell", result.message.lower())


if __name__ == "__main__":
    unittest.main()
