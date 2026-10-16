import tempfile
import unittest
from pathlib import Path

from zeus_pc.persistence import ZeusDatabase
from zeus_pc.report import build_report_html


SCAN = {
    "id": 7,
    "created_at": "2026-10-13T21:04:00",
    "profile": "GENERAL",
    "snapshot": [
        {
            "domain": "Windows Features",
            "status": "OK",
            "message": "",
            "metrics": [
                {"name": "count", "value": 136, "source": "Win32_OptionalFeature"},
                {"name": "items", "value": [{"name": "TelnetClient"}], "source": "Win32_OptionalFeature"},
            ],
        },
        {
            "domain": "Secure Boot",
            "status": "REQUIRES_ELEVATION",
            "message": "A leitura exige administrador.",
            "metrics": [],
        },
    ],
    "recommendations": [
        {"title": "Manter o plano atual", "reason": "Ja esta correto", "status": "ALREADY_CORRECT"}
    ],
}


class ReportHtmlTests(unittest.TestCase):
    def build(self, scan=SCAN):
        return build_report_html(scan, version="0.9.0", build="20261013")

    def test_contains_scan_identity_and_profile(self):
        page = self.build()
        self.assertIn("Relatorio da analise #7", page)
        self.assertIn("Perfil GENERAL", page)
        self.assertIn("0.9.0", page)

    def test_renders_honest_status_labels(self):
        page = self.build()
        self.assertIn("Requer administrador", page)
        self.assertIn("Windows Features", page)
        self.assertIn("A leitura exige administrador.", page)
        self.assertIn("1 OK", page)

    def test_escapes_html_in_dynamic_values(self):
        hostile = {
            "id": 1,
            "created_at": "x",
            "profile": "<script>alert(1)</script>",
            "snapshot": [
                {
                    "domain": "CPU<script>",
                    "status": "OK",
                    "message": "",
                    "metrics": [{"name": "model", "value": "<img src=x>", "source": "t"}],
                }
            ],
            "recommendations": [{"title": "<b>x</b>", "reason": "&", "status": "RECOMMENDED"}],
        }
        page = self.build(hostile)
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img src=x>", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_empty_scan_renders_placeholders(self):
        page = self.build({"id": 2, "created_at": None, "profile": None, "snapshot": [], "recommendations": []})
        self.assertIn("Nenhuma recomendacao gerada", page)
        self.assertIn("Nenhum dominio registrado", page)


class GetScanTests(unittest.TestCase):
    def test_get_scan_returns_stored_scan_and_none_for_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = ZeusDatabase(Path(tmp) / "zeus.db")
            db.initialize()
            db.record_scan("GENERAL", [{"domain": "CPU", "status": "OK", "metrics": []}], [])
            stored = db.get_scan(1)
            self.assertEqual(stored["id"], 1)
            self.assertEqual(stored["profile"], "GENERAL")
            self.assertIsNone(db.get_scan(999))
            self.assertIsNone(db.get_scan("abc"))


if __name__ == "__main__":
    unittest.main()
