"""Output decoding for PowerShell (fix: accented plan names corrupted).

Windows PowerShell writes redirected pipes in the console OEM codepage
(cp850 on pt-BR, where 'a-acute' is 0xA0) while subprocess text mode
reads the ANSI codepage (cp1252, where 0xA0 is a non-breaking space),
so power plan names like "Desempenho Máximo" reached the API as
"Desempenho M\\xa0ximo". _decode_output must recover the real text.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from zeus_pc.powershell import _decode_output


class DecodeOutputTests(unittest.TestCase):
    def test_utf8_output_passes_through(self) -> None:
        self.assertEqual(
            _decode_output("Desempenho Máximo".encode("utf-8")),
            "Desempenho Máximo",
        )

    def test_utf8_bom_is_stripped(self) -> None:
        payload = b"\xef\xbb\xbf" + "Alto desempenho".encode("utf-8")
        self.assertEqual(_decode_output(payload), "Alto desempenho")

    @patch("zeus_pc.powershell._oem_encoding", return_value="cp850")
    def test_oem_cp850_accented_names_are_recovered(self, _oem) -> None:
        # cp850: a-acute=0xA0, c-cedilla=0x87, i-acute=0xA1. UTF-8
        # rejects these bytes, so the OEM candidate must be used.
        payload = "Desempenho Máximo · Serviço de Energia · Início".encode("cp850")
        self.assertEqual(
            _decode_output(payload),
            "Desempenho Máximo · Serviço de Energia · Início",
        )

    def test_ansi_fallback_answers_when_no_oem_is_declared(self) -> None:
        # 0xA0 alone is invalid UTF-8; on non-Windows (no OEM) cp1252
        # is the declared fallback before latin-1.
        with patch("zeus_pc.powershell._oem_encoding", return_value=None):
            self.assertEqual(_decode_output(b"M\xa0ximo"), "M\xa0ximo")

    def test_empty_payload_returns_empty_string(self) -> None:
        self.assertEqual(_decode_output(b""), "")

    @patch("zeus_pc.powershell._oem_encoding", return_value=None)
    def test_without_oem_utf8_still_wins_over_cp1252(self, _oem) -> None:
        # 0xC3 0xA9 is "é" in UTF-8 but "Ã©" in cp1252: UTF-8 must
        # be preferred so pwsh-style output is not mangled.
        self.assertEqual(_decode_output("É".encode("utf-8")), "É")


if __name__ == "__main__":
    unittest.main()
