import datetime as dt
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from zeus_pc import server
from zeus_pc.app_logging import StructuredLog
from zeus_pc.drivers import (
    OLD_DRIVER_DAYS,
    WINDOWS_DRIVER_EPOCH,
    _annotate_store,
    _codepage,
    _decode_for_labels,
    classify_device,
    normalize_device,
    parse_driver_date,
    parse_pnputil_drivers,
)
from zeus_pc.persistence import ZeusDatabase
from zeus_pc.transaction import TransactionEngine

PNPUTIL_PT_BR = """\
Nome publicado:     oem47.inf
Nome original:      nv_dispi.inf
Nome do provedor:   NVIDIA
Nome da classe:     Display
GUID da classe:     {4d36e968-e325-11ce-bfc1-08002be10318}
Versão do driver:   32.0.15.7602
Data do driver:     2025-05-14
Nome do assinante:  NVIDIA Corporation

Nome publicado:     oem52.inf
Nome original:      mixedmode.inf
Nome do provedor:   Intel
Nome da classe:     System
GUID da classe:     {4d36e97d-e325-11ce-bfc1-08002be10318}
Versão do driver:   10.1.41.5
Nome do assinante:
"""

# Formato real observado nesta maquina pt-BR (Windows 11): rotulos com
# acento, "Nome do Signatario", "GUID de Classe" e a data embutida na
# propria linha da versao, em formato americano.
PNPUTIL_PT_BR_REAL = """\
Nome publicado:                   oem61.inf
Nome original:                    intelccp.inf
Nome do provedor:                 Intel
Nome da Classe:                   SoftwareComponent
GUID de Classe:                   {5c4c3332-344d-483c-8739-259e934c9cc8}
Versao do Driver:                 10/15/2024 1.0.0.1052
Nome do Signatario:               INTEL(R) CORPORATION
Atributos:                        0x8000000

Nome publicado:                   oem77.inf
Nome original:                    oem77.inf
Nome do provedor:                 Microsoft
Nome da Classe:                   System
GUID de Classe:                   {4d36e97d-e325-11ce-bfc1-08002be10318}
Versao do Driver:                 10.0.22621.1
Nome do Signatario:               Microsoft Windows
"""

PNPUTIL_EN_US = """\
Published Name:     oem12.inf
Original Name:      rt640x64.inf
Provider Name:      Realtek
Class Name:         Net
Class GUID:         {4d36e972-e325-11ce-bfc1-08002be10318}
Driver Version:     10.68.915.2024
Signer Name:        Microsoft Windows Hardware Compatibility Publisher
"""


class ParsePnpUtilTests(unittest.TestCase):
    def test_parses_pt_br_blocks_and_flags_missing_signer(self):
        entries = parse_pnputil_drivers(PNPUTIL_PT_BR)
        self.assertEqual(len(entries), 2)
        first, second = entries
        self.assertEqual(first["published"], "oem47.inf")
        self.assertEqual(first["provider"], "NVIDIA")
        self.assertEqual(first["version"], "32.0.15.7602")
        self.assertEqual(first["signer"], "NVIDIA Corporation")
        self.assertEqual(second["published"], "oem52.inf")
        self.assertEqual(second.get("signer"), "")

    def test_parses_en_us_labels(self):
        entries = parse_pnputil_drivers(PNPUTIL_EN_US)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["published"], "oem12.inf")
        self.assertEqual(entry["provider"], "Realtek")
        self.assertEqual(entry["class_name"], "Net")
        self.assertEqual(entry["signer"], "Microsoft Windows Hardware Compatibility Publisher")

    def test_unknown_labels_do_not_break_blocks(self):
        text = PNPUTIL_EN_US.replace(
            "Signer Name:        Microsoft Windows Hardware Compatibility Publisher",
            "Something New:      whatever\nSigner Name:        Microsoft Windows",
        )
        entries = parse_pnputil_drivers(text)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("Something New" , entries[0])
        self.assertEqual(entries[0]["signer"], "Microsoft Windows")

    def test_empty_and_junk_input_returns_nothing(self):
        self.assertEqual(parse_pnputil_drivers(""), [])
        self.assertEqual(parse_pnputil_drivers("apenas um texto solto sem rotulo"), [])


class RealPnpFormatTests(unittest.TestCase):
    """O formato que esta maquina pt-BR realmente produz."""

    def test_parses_accented_labels_and_embedded_version_date(self):
        entries = parse_pnputil_drivers(PNPUTIL_PT_BR_REAL)
        self.assertEqual(len(entries), 2)
        first, second = entries
        self.assertEqual(first["published"], "oem61.inf")
        self.assertEqual(first["class_guid"], "{5c4c3332-344d-483c-8739-259e934c9cc8}")
        self.assertEqual(first["signer"], "INTEL(R) CORPORATION")
        self.assertEqual(first["version"], "10/15/2024 1.0.0.1052")
        self.assertEqual(second["signer"], "Microsoft Windows")

    def test_annotate_splits_embedded_date_from_version(self):
        entry = parse_pnputil_drivers(PNPUTIL_PT_BR_REAL)[0]
        annotated = _annotate_store(entry)
        self.assertEqual(annotated["version"], "1.0.0.1052")
        self.assertEqual(annotated["date"], "2024-10-15")
        self.assertFalse(annotated["unsigned"])

    def test_annotate_keeps_version_when_no_date_embedded(self):
        entry = parse_pnputil_drivers(PNPUTIL_PT_BR_REAL)[1]
        annotated = _annotate_store(entry)
        self.assertEqual(annotated["version"], "10.0.22621.1")
        self.assertIsNone(annotated["date"])

    def test_annotate_drops_placeholder_1968_date(self):
        # O INF da Intel desta maquina reporta "07/18/1968" — datas-nula
        # do WDK, nao idade do driver.
        text = (
            "Nome publicado: oem1.inf\n"
            "Versao do Driver: 07/18/1968 10.1.14.7\n"
        )
        annotated = _annotate_store(parse_pnputil_drivers(text)[0])
        self.assertEqual(annotated["version"], "10.1.14.7")
        self.assertIsNone(annotated["date"])


class DecodeForLabelsTests(unittest.TestCase):
    """O mesmo byte significa coisas diferentes em OEM e ANSI; o decode
    tem que ser escolhido por evidencia (rotulos conhecidos)."""

    @unittest.skipUnless(os.name == "nt", "codepages Windows")
    def test_prefers_the_decode_that_keeps_known_labels(self):
        text = "Nome do Signatário: INTEL(R) CORPORATION\n"
        # As ferramentas do Windows escrevem no OEM (console) ou no ANSI
        # (processo) da MÁQUINA: testamos os codepages reais de cada
        # sistema — cp850/cp1252 nesta máquina pt-BR, cp437/cp1252 no
        # runner en-US (que nem tem 'ã' no cp437, por isso o acento em
        # 'á', presente em todos). Codificar em cp850 fixo como antes só
        # funcionava em máquinas pt-BR.
        checked = 0
        for name in ("GetOEMCP", "GetACP"):
            encoding = _codepage(name)
            if not encoding:
                continue
            try:
                raw = text.encode(encoding)
            except UnicodeEncodeError:
                continue  # a codepage desta máquina não tem o acento
            decoded = _decode_for_labels(raw)
            self.assertIn("Signatário", decoded, encoding)
            checked += 1
        self.assertGreaterEqual(checked, 1)

    def test_scoring_is_deterministic_for_pt_br_and_en_us_pairs(self):
        # Sem depender da máquina de teste: simula os dois pares reais.
        # pt-BR: ANSI cp1252 com OEM cp850; en-US: ANSI cp1252 com
        # OEM cp437 (não tem 'ã', por isso o braço en-US só usa 'á').
        signatario = "Nome do Signatário: INTEL\n"
        cases = (
            ("cp1252", signatario + "Versão do Driver:  1.0.0.1\n", "cp850", "cp1252"),
            ("cp850", signatario + "Versão do Driver:  1.0.0.1\n", "cp850", "cp1252"),
            ("cp437", signatario, "cp437", "cp1252"),
        )
        for raw_encoding, text, oem, ansi in cases:
            try:
                raw = text.encode(raw_encoding)
            except UnicodeEncodeError:
                continue
            with patch(
                "zeus_pc.drivers._codepage",
                side_effect=lambda name, oem=oem, ansi=ansi: {
                    "GetOEMCP": oem,
                    "GetACP": ansi,
                }.get(name),
            ):
                decoded = _decode_for_labels(raw)
            self.assertIn("Signatário", decoded, raw_encoding)

    def test_utf8_is_used_when_valid(self):
        text = "Nome do Signatário: INTEL\n"
        self.assertIn("Signatário", _decode_for_labels(text.encode("utf-8")))

    def test_empty_input(self):
        self.assertEqual(_decode_for_labels(b""), "")


class DriverDateTests(unittest.TestCase):
    def test_parses_dotnet_json_date(self):
        # 2024-03-15 00:00:00 UTC em epoch-ms
        self.assertEqual(
            parse_driver_date("/Date(1710460800000)/"),
            dt.date(2024, 3, 15),
        )

    def test_parses_iso_and_dmtf_and_epoch(self):
        self.assertEqual(parse_driver_date("2024-03-15T00:00:00"), dt.date(2024, 3, 15))
        self.assertEqual(parse_driver_date("20240315000000.000000+000"), dt.date(2024, 3, 15))
        self.assertEqual(parse_driver_date("1710460800000"), dt.date(2024, 3, 15))

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_driver_date(None))
        self.assertIsNone(parse_driver_date(""))
        self.assertIsNone(parse_driver_date("ontem"))
        self.assertIsNone(parse_driver_date("/Date(nao-numero)/"))


class ClassifyDeviceTests(unittest.TestCase):
    TODAY = dt.date(2026, 10, 21)

    def test_is_signed_false_is_unsigned(self):
        self.assertEqual(classify_device(False, dt.date(2026, 1, 1), self.TODAY), "UNSIGNED")

    def test_missing_is_signed_is_unknown_not_unsigned(self):
        self.assertEqual(classify_device(None, dt.date(2026, 1, 1), self.TODAY), "UNKNOWN_SIGN")

    def test_signed_old_beyond_threshold_is_old(self):
        old_date = self.TODAY - dt.timedelta(days=OLD_DRIVER_DAYS + 1)
        self.assertEqual(classify_device(True, old_date, self.TODAY), "OLD")

    def test_signed_recent_is_ok_and_boundary_is_ok(self):
        self.assertEqual(classify_device(True, self.TODAY - dt.timedelta(days=10), self.TODAY), "OK")
        boundary = self.TODAY - dt.timedelta(days=OLD_DRIVER_DAYS)
        self.assertEqual(classify_device(True, boundary, self.TODAY), "OK")

    def test_signed_without_date_is_ok_not_old(self):
        self.assertEqual(classify_device(True, None, self.TODAY), "OK")

    def test_windows_inbox_epoch_date_is_not_old(self):
        # Drivers inbox do Windows reportam 2006-06-21 quando o INF nao
        # traz data real: e "data nao informada", nao idade.
        self.assertEqual(WINDOWS_DRIVER_EPOCH, dt.date(2006, 6, 21))
        self.assertEqual(
            classify_device(True, WINDOWS_DRIVER_EPOCH, self.TODAY), "OK"
        )
        # data real logo apos a epoch continua sendo antiga de verdade
        dia_seguinte = WINDOWS_DRIVER_EPOCH + dt.timedelta(days=1)
        self.assertEqual(classify_device(True, dia_seguinte, self.TODAY), "OLD")

    def test_other_placeholder_dates_are_not_old(self):
        # 1970-01-01 = DriverDate=0 (epoch Unix) observado no WMI;
        # 1968-07-18 = datas-nula do WDK; 1601-01-01 = epoch do FILETIME.
        for placeholder in (dt.date(1970, 1, 1), dt.date(1968, 7, 18), dt.date(1601, 1, 1)):
            self.assertEqual(
                classify_device(True, placeholder, self.TODAY), "OK", placeholder
            )


class NormalizeDeviceTests(unittest.TestCase):
    def test_normalizes_fields_and_status(self):
        item = normalize_device(
            {
                "DeviceName": "NVIDIA GeForce RTX 4060",
                "DeviceClass": "DISPLAY",
                "DriverProviderName": "NVIDIA",
                "DriverVersion": "32.0.15.7602",
                "DriverDate": "/Date(1710460800000)/",
                "IsSigned": True,
            },
            dt.date(2026, 10, 21),
        )
        self.assertEqual(item["device_name"], "NVIDIA GeForce RTX 4060")
        self.assertEqual(item["provider"], "NVIDIA")
        self.assertEqual(item["version"], "32.0.15.7602")
        self.assertEqual(item["date"], "2024-03-15")
        self.assertIs(item["signed"], True)
        self.assertEqual(item["status"], "OK")

    def test_is_signed_absent_maps_to_none(self):
        item = normalize_device({"DeviceName": "X"}, dt.date(2026, 10, 21))
        self.assertIsNone(item["signed"])
        self.assertEqual(item["status"], "UNKNOWN_SIGN")

    def test_placeholder_date_becomes_none_not_old(self):
        # WMI real desta maquina: DriverDate=0 vira /Date(0)/ = 1970-01-01
        # (Intel Gaussian Mixture Model) — "desde —", nao "antigo".
        item = normalize_device(
            {
                "DeviceName": "Intel(R) GMM",
                "IsSigned": True,
                "DriverDate": "/Date(0)/",
            },
            dt.date(2026, 10, 21),
        )
        self.assertIsNone(item["date"])
        self.assertEqual(item["status"], "OK")


class DriversApiTests(unittest.TestCase):
    """GET /api/drivers against a real Handler, collectors patched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        db = ZeusDatabase(root / "zeus.db")
        db.initialize()
        app_log = StructuredLog(root / "app.jsonl", max_entries=200)

        for name, value in (
            ("DB", db),
            ("TRANSACTIONS", TransactionEngine(db)),
            ("APP_LOG", app_log),
        ):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        inventory = {
            "store": [
                {
                    "published": "oem47.inf",
                    "original": "nv_dispi.inf",
                    "provider": "NVIDIA",
                    "class_name": "Display",
                    "class_guid": "{4d36e968-e325-11ce-bfc1-08002be10318}",
                    "version": "32.0.15.7602",
                    "date": "2025-05-14",
                    "signer": "NVIDIA Corporation",
                    "unsigned": False,
                },
                {
                    "published": "oem52.inf",
                    "original": "mixedmode.inf",
                    "provider": "Intel",
                    "class_name": "System",
                    "class_guid": "{4d36e97d-e325-11ce-bfc1-08002be10318}",
                    "version": "10.1.41.5",
                    "date": None,
                    "signer": None,
                    "unsigned": True,
                },
            ],
            "devices": [
                {
                    "device_name": "Placa de rede",
                    "provider": "Realtek",
                    "class_name": "Net",
                    "version": "10.68.915.2024",
                    "date": "2024-01-02",
                    "signed": False,
                    "status": "UNSIGNED",
                },
            ],
            "summary": {
                "device_count": 1,
                "unsigned_count": 1,
                "unknown_sign_count": 0,
                "old_count": 0,
                "store_count": 2,
                "store_unsigned_count": 1,
                "checked_at": "2026-10-21T00:00:00+00:00",
                "collected_for_date": "2026-10-21",
            },
            "old_after_days": OLD_DRIVER_DAYS,
        }
        collector = patch.object(
            server, "collect_driver_inventory", return_value=inventory
        )
        collector.start()
        self.addCleanup(collector.stop)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method: str, path: str, *, host: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if host is not None:
            headers["Host"] = host
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_get_returns_inventory_summary_and_lists(self):
        status, headers, payload = self.request("GET", "/api/drivers")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        data = json.loads(payload)
        self.assertEqual(data["summary"]["store_count"], 2)
        self.assertEqual(data["summary"]["store_unsigned_count"], 1)
        self.assertEqual(data["summary"]["unsigned_count"], 1)
        self.assertEqual(len(data["devices"]), 1)
        self.assertEqual(data["devices"][0]["status"], "UNSIGNED")
        self.assertEqual(len(data["store"]), 2)
        self.assertTrue(data["store"][1]["unsigned"])
        self.assertEqual(data["old_after_days"], OLD_DRIVER_DAYS)

    def test_get_is_guarded_like_every_other_route(self):
        status, _, payload = self.request(
            "GET", "/api/drivers", host="evil.example.com:8000"
        )
        self.assertEqual(status, 403)
        self.assertIn("Host negado", json.loads(payload)["message"])


if __name__ == "__main__":
    unittest.main()
