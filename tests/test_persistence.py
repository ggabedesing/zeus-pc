import tempfile
import unittest
from pathlib import Path

from zeus_pc.persistence import SCHEMA_VERSION, ZeusDatabase


class PersistenceTests(unittest.TestCase):
    def test_initialize_creates_versioned_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            health = db.initialize()
            self.assertTrue(Path(health.path).exists())
            self.assertTrue(health.writable)
            self.assertEqual(health.schema_version, SCHEMA_VERSION)

    def test_setting_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            db.set_setting("profile", {"name": "GAMING", "locked": True})
            self.assertEqual(db.get_setting("profile"), {"name": "GAMING", "locked": True})

    def test_scan_history_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            scan_id = db.record_scan("gaming", [{"domain": "CPU"}], [{"id": "system.no-action.v1"}])
            rows = db.recent_scans()
            self.assertEqual(rows[0]["id"], scan_id)
            self.assertEqual(rows[0]["profile"], "GAMING")


if __name__ == "__main__":
    unittest.main()
