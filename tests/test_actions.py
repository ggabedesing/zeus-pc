import os
import unittest
from unittest.mock import patch

from zeus_pc.actions import GUID_RE, decode_console_output, list_power_plans


class ActionValidationTests(unittest.TestCase):
    def test_valid_guid(self):
        self.assertTrue(GUID_RE.fullmatch("381b4222-f694-41f0-9685-ff5bb260df2e"))

    def test_rejects_shell_text(self):
        self.assertFalse(GUID_RE.fullmatch("381b4222-f694-41f0-9685-ff5bb260df2e & whoami"))

    def test_empty_output_decodes_to_empty_text(self):
        self.assertEqual(decode_console_output(b""), "")

    @unittest.skipUnless(os.name == "nt", "oem codec exists only on Windows")
    def test_oem_accent_bytes_are_decoded(self):
        self.assertEqual(
            decode_console_output(b"(Desempenho M\xa0ximo)"),
            "(Desempenho Máximo)",
        )

    def test_non_decodable_bytes_do_not_raise(self):
        self.assertIn("ximo", decode_console_output(b"(Desempenho M?ximo)"))

    @patch("zeus_pc.actions.subprocess.run")
    def test_power_plan_names_keep_accents(self, run):
        run.return_value = type(
            "Proc",
            (),
            {
                "returncode": 0,
                "stdout": (
                    b"\r\nExisting Power Schemes (* Active)\r\n"
                    b"GUID do Esquema de Energia: "
                    b"e3614da8-3d61-420c-8250-9724d15cfb0e"
                    b"  (Desempenho M\xa0ximo) *\r\n"
                ),
                "stderr": b"",
            },
        )()

        plans = list_power_plans()

        self.assertEqual(len(plans), 1)
        self.assertEqual(
            plans[0]["guid"],
            "e3614da8-3d61-420c-8250-9724d15cfb0e",
        )
        self.assertTrue(plans[0]["active"])
        if os.name == "nt":
            self.assertEqual(plans[0]["name"], "Desempenho Máximo")
        else:
            self.assertIn("Desempenho", plans[0]["name"])


if __name__ == "__main__":
    unittest.main()
