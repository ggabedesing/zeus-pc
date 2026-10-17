import tempfile
import unittest
from pathlib import Path

from zeus_pc.config import CONFIG_SCHEMA_VERSION, load_config, save_config
from zeus_pc.persistence import ZeusDatabase


class ConfigTests(unittest.TestCase):
    def test_defaults_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            config = load_config(db)
            self.assertEqual(config.schema_version, CONFIG_SCHEMA_VERSION)
            self.assertEqual(config.selected_profile, "GENERAL")
            self.assertEqual(config.history_limit, 20)

    def test_partial_update_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            save_config(db, {"history_limit": 55})
            config = save_config(db, {"selected_profile": "gaming"})
            self.assertEqual(config.selected_profile, "GAMING")
            self.assertEqual(config.history_limit, 55)

    def test_invalid_profile_falls_back_to_general(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            config = save_config(db, {"selected_profile": "invalid"})
            self.assertEqual(config.selected_profile, "GENERAL")

    def test_unknown_schema_resets_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            config = save_config(db, {"schema_version": 999, "selected_profile": "GAMING"})
            self.assertEqual(config.schema_version, CONFIG_SCHEMA_VERSION)
            self.assertEqual(config.selected_profile, "GENERAL")


if __name__ == "__main__":
    unittest.main()
