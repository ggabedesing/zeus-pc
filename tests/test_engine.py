import unittest

from zeus_pc.engine import build_recommendations
from zeus_pc.models import Metric, ProbeResult, Status


class EngineTests(unittest.TestCase):
    def test_low_storage_is_recommended(self):
        snapshot = [ProbeResult("Storage", Status.OK, [Metric("volumes", [{"root": "C:\\", "free_pct": 8.0}])])]
        result = build_recommendations(snapshot)
        self.assertEqual(result[0].id, "storage.low-space")

    def test_no_action_when_nothing_is_wrong(self):
        snapshot = [ProbeResult("Storage", Status.OK, [Metric("volumes", [{"root": "C:\\", "free_pct": 80.0}])])]
        result = build_recommendations(snapshot)
        self.assertEqual(result[0].status, "ALREADY_CORRECT")


if __name__ == "__main__":
    unittest.main()
