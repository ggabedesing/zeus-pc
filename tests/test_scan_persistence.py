import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeus_pc.app_logging import StructuredLog
from zeus_pc.models import Metric, ProbeResult, Recommendation, Status
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.server import run_scan


class ScanPersistenceIntegrationTests(unittest.TestCase):
    def test_run_scan_persists_result_and_returns_scan_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            app_log = StructuredLog(Path(tmp) / "app.jsonl")
            snapshot = [
                ProbeResult(
                    "CPU",
                    Status.OK,
                    [Metric("name", "Test CPU")],
                    source="test",
                )
            ]
            recommendations = [
                Recommendation(
                    "system.no-action.v1",
                    "No action",
                    "SYSTEM",
                    "ALREADY_CORRECT",
                    "Test",
                    "NONE",
                )
            ]

            with patch("zeus_pc.server.collect_all", return_value=snapshot), patch(
                "zeus_pc.server.build_recommendations",
                return_value=recommendations,
            ):
                payload = run_scan("gaming", db, app_log)

            self.assertEqual(payload["scan_id"], 1)
            self.assertEqual(payload["profile"], "GAMING")

            history = db.recent_scans()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["profile"], "GAMING")
            self.assertEqual(history[0]["snapshot"][0]["domain"], "CPU")
            self.assertEqual(
                history[0]["recommendations"][0]["id"],
                "system.no-action.v1",
            )
            records = app_log.recent()
            self.assertEqual(records[-1]["event"], "scan_completed")


if __name__ == "__main__":
    unittest.main()
