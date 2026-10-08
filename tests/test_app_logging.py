import tempfile
import unittest
from pathlib import Path

from zeus_pc.app_logging import StructuredLog


class StructuredLogTests(unittest.TestCase):
    def test_write_and_read_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = StructuredLog(Path(tmp) / "app.jsonl", max_entries=100)
            log.write("server_started", version="0.1")
            records = log.recent()
            self.assertEqual(records[-1]["event"], "server_started")
            self.assertEqual(records[-1]["context"]["version"], "0.1")

    def test_retention_keeps_latest_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = StructuredLog(Path(tmp) / "app.jsonl", max_entries=100)
            for index in range(105):
                log.write("sample", index=index)
            records = log.recent(200)
            self.assertEqual(len(records), 100)
            self.assertEqual(records[0]["context"]["index"], 5)
            self.assertEqual(records[-1]["context"]["index"], 104)

    def test_empty_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = StructuredLog(Path(tmp) / "app.jsonl")
            with self.assertRaises(ValueError):
                log.write("")


if __name__ == "__main__":
    unittest.main()
