"""Inventario de drivers estilo Driver Booster (somente leitura).

Duas fontes complementares, ambas sem privilegios especiais:

* ``pnputil /enum-drivers`` — pacotes da loja de drivers (oem*.inf) com
  provedor, classe, versao e assinante. A saida vem no console OEM do
  Windows (cp850 em pt-BR), por isso passamos por ``_decode_output`` e os
  rotulos sao aceitos em pt-BR e en-US.
* ``Win32_PnPSignedDriver`` — cada dispositivo com o driver aplicado,
  versao, data e ``IsSigned`` (True/False/ausente).

Classificacao honesta — sem catalogo online ainda (discover de updates e
o proximo incremento):

* ``UNSIGNED``      IsSigned e False de verdade;
* ``UNKNOWN_SIGN``  IsSigned ausente/null (Windows nao afirmou nada);
* ``OLD``           data do driver com mais de 3 anos (1095 dias);
* ``OK``            assinado e dentro da janela de idade.

Duas honestidades aprendidas com a maquina real (pt-BR, Windows 11):

* o ``pnputil`` escreve no **ANSI** (cp1252: 'a' com til = 0xE3), nao no
  OEM como o powercfg — e em cp850 esse mesmo byte e 'O com grave'. Como
  a ferramenta nao anuncia a codepage, o decode e escolhido por evidencia:
  o candidato (UTF-8, OEM, ANSI, latin-1) com mais rotulos conhecidos.
* drivers cuja "data" e um placeholder do sistema — a epoch do WDK
  (1968-06-21/1968-07-18), o epoch Unix 1970-01-01 (DriverDate=0 no
  WMI) e a baseline dos drivers inbox do Windows (2006-06-21) — nao
  tem idade real; trata-las como "antigo" geraria falsos positivos
  (nesta maquina: de 127 para 24), entao elas viram "data nao
  informada" e saem do teste de idade.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import re
import subprocess
import unicodedata

from .powershell import run_json

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

OLD_DRIVER_DAYS = 1095  # ~3 anos

# Rotulos do pnputil /enum-drivers, normalizados (minusculas, sem acento).
_STORE_LABELS = {
    "nome publicado": "published",
    "published name": "published",
    "nome original": "original",
    "original name": "original",
    "nome do provedor": "provider",
    "provider name": "provider",
    "nome da classe": "class_name",
    "class name": "class_name",
    "guid de classe": "class_guid",
    "guid da classe": "class_guid",
    "class guid": "class_guid",
    "versao do driver": "version",
    "driver version": "version",
    "data do driver": "date",
    "driver date": "date",
    "nome do signatario": "signer",
    "nome do assinante": "signer",
    "signer name": "signer",
    "provedor da extensao": "extension_provider",
    "extension provider": "extension_provider",
}

STATUS_ORDER = {"UNSIGNED": 0, "UNKNOWN_SIGN": 1, "OLD": 2, "OK": 3}

# Datas que o Windows reporta como "nao informada" — nenhuma pode ser
# idade real de driver: 1601-01-01 e a epoch do FILETIME, 1968 sao as
# datas-nula do WDK (1968-07-18 observada nesta maquina no INF da
# Intel), 1970-01-01 vem de DriverDate=0 (epoch Unix no WMI) e
# 2006-06-21 e a baseline dos drivers inbox do Windows.
WINDOWS_DRIVER_EPOCH = dt.date(2006, 6, 21)
NULL_DRIVER_DATES = frozenset(
    {
        dt.date(1601, 1, 1),
        dt.date(1968, 6, 21),
        dt.date(1968, 7, 18),
        dt.date(1970, 1, 1),
        WINDOWS_DRIVER_EPOCH,
    }
)

# Rotulos conhecidos em pt-BR e en-US, sem acento, para pontuar decodes.
_DECODE_MARKERS = (
    "nome publicado", "published name",
    "nome original", "original name",
    "nome do provedor", "provider name",
    "nome da classe", "class name",
    "nome do signatario", "signer name",
    "versao do driver", "driver version",
)

# O pnputil pt-BR embute a data na propria linha de versao,
# em formato americano: "07/18/1968 10.1.14.7".
_VERSION_WITH_DATE_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\S+)\s*$"
)

STATUS_LABELS = {
    "UNSIGNED": "sem assinatura",
    "UNKNOWN_SIGN": "assinatura desconhecida",
    "OLD": "antigo",
    "OK": "ok",
}


def _fold_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )


def _normalize_label(value: str) -> str:
    text = _fold_accents(str(value or ""))
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(":").strip()


def _codepage(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        codepage = getattr(ctypes.windll.kernel32, name)()
    except Exception:
        return None
    return f"cp{codepage}" if codepage else None


def _decode_for_labels(raw: bytes) -> str:
    """Escolhe o decode por evidencia, nao por suposicao.

    O mesmo byte 0xE3 e 'a' com til em cp1252 (ANSI, o que o pnputil
    escreve) e 'O com grave' em cp850 (OEM): ambos decodificam sem erro,
    entao a ordem de tentativas decide errado. Pontuamos cada candidato
    contando rotulos conhecidos (ja sem acento) presentes no texto.
    """
    if not raw:
        return ""
    candidates = ["utf-8-sig"]
    for encoding in ("GetOEMCP", "GetACP"):
        value = _codepage(encoding)
        if value and value not in candidates:
            candidates.append(value)
    candidates.append("latin-1")

    best_text = ""
    best_score = -1
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        folded = _fold_accents(text).casefold()
        score = sum(1 for marker in _DECODE_MARKERS if marker in folded)
        if score > best_score:
            best_text, best_score = text, score
    return best_text


def _parse_mdY(value: str) -> str | None:
    """M/d/yyyy (formato do proprio pnputil) -> ISO; ambiguo -> None."""
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", str(value or "").strip())
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_iso_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def parse_pnputil_drivers(text: str) -> list[dict[str, str]]:
    """Blocos ``Rotulo: valor`` do ``pnputil /enum-drivers``.

    Aceita pt-BR e en-US; rotulos desconhecidos sao ignorados sem quebrar
    o bloco (builds novas do Windows adicionam campos).
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _STORE_LABELS.get(_normalize_label(label))
        if key:
            current[key] = value.strip()
    if current:
        entries.append(current)
    return [
        entry
        for entry in entries
        if entry.get("published") or entry.get("original")
    ]


def parse_driver_date(value) -> dt.date | None:
    """``/Date(ms)/`` (PS 5.1), ISO, DMTF ou epoch-ms puro -> data."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"/Date\((-?\d+)\)/", text)
    if match:
        try:
            return dt.datetime.fromtimestamp(
                int(match.group(1)) / 1000,
                tz=dt.timezone.utc,
            ).date()
        except (OverflowError, OSError, ValueError):
            return None
    match = re.match(
        r"^(\d{4})(\d{2})(\d{2})\d{6}(?:\.\d+)?(?:[+-]\d{3})?$",
        text,
    )
    if match:  # DMTF: yyyymmddHHMMSS.mmmmmmsUUU
        try:
            return dt.date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None
    if re.fullmatch(r"\d{13}", text):
        try:
            return dt.datetime.fromtimestamp(
                int(text) / 1000,
                tz=dt.timezone.utc,
            ).date()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def classify_device(
    signed: bool | None,
    driver_date: dt.date | None,
    today: dt.date,
) -> str:
    if signed is False:
        return "UNSIGNED"
    if signed is None:
        return "UNKNOWN_SIGN"
    if (
        driver_date is not None
        and driver_date not in NULL_DRIVER_DATES
        and (today - driver_date).days > OLD_DRIVER_DAYS
    ):
        return "OLD"
    return "OK"


def normalize_device(record: dict, today: dt.date) -> dict:
    signed_raw = record.get("IsSigned")
    signed = None if signed_raw is None else bool(signed_raw)
    driver_date = parse_driver_date(record.get("DriverDate"))
    if driver_date in NULL_DRIVER_DATES:
        driver_date = None  # "desde —": data reportada e placeholder
    return {
        "device_name": record.get("DeviceName"),
        "provider": record.get("DriverProviderName"),
        "class_name": record.get("DeviceClass"),
        "version": record.get("DriverVersion"),
        "date": driver_date.isoformat() if driver_date else None,
        "signed": signed,
        "status": classify_device(signed, driver_date, today),
    }


def _annotate_store(entry: dict[str, str]) -> dict:
    signer = (entry.get("signer") or "").strip()
    version = (entry.get("version") or "").strip() or None
    date = (entry.get("date") or "").strip() or None
    if version:
        embedded = _VERSION_WITH_DATE_RE.match(version)
        if embedded and not date:
            parsed = _parse_mdY(
                f"{embedded.group(1)}/{embedded.group(2)}/{embedded.group(3)}"
            )
            if parsed:
                date = parsed
            version = embedded.group(4)
    if date and _parse_iso_date(date) in NULL_DRIVER_DATES:
        date = None
    return {
        "published": entry.get("published"),
        "original": entry.get("original"),
        "provider": entry.get("provider"),
        "class_name": entry.get("class_name"),
        "class_guid": entry.get("class_guid"),
        "version": version,
        "date": date,
        "signer": signer or None,
        "unsigned": not signer,
    }


def collect_driver_inventory(today: dt.date | None = None) -> dict:
    """Loja (pnputil) + dispositivos (Win32_PnPSignedDriver) + resumo."""
    today = today or dt.datetime.now(dt.timezone.utc).date()

    store: list[dict] = []
    store_error = ""
    devices: list[dict] = []
    device_error = ""

    if os.name != "nt":
        return {
            "store": [],
            "devices": [],
            "summary": _build_summary(store, devices, today),
            "old_after_days": OLD_DRIVER_DAYS,
            "message": "Disponivel apenas no Windows.",
        }

    try:
        proc = subprocess.run(
            ["pnputil", "/enum-drivers"],
            capture_output=True,
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode != 0:
            store_error = (
                _decode_for_labels(proc.stderr).strip()
                or f"pnputil retornou {proc.returncode}"
            )
        else:
            store = [
                _annotate_store(entry)
                for entry in parse_pnputil_drivers(
                    _decode_for_labels(proc.stdout)
                )
            ]
    except Exception as exc:  # noqa: BLE001 - inventario e melhor-esforco
        store_error = str(exc)

    try:
        raw = run_json(
            "Get-CimInstance Win32_PnPSignedDriver | "
            "Select-Object DeviceName,DeviceClass,DriverProviderName,"
            "DriverVersion,DriverDate,IsSigned",
            timeout=15,
        )
        if isinstance(raw, dict):
            raw = [raw]
        devices = [normalize_device(item, today) for item in (raw or [])]
        devices.sort(
            key=lambda d: (
                STATUS_ORDER.get(d["status"], 9),
                (d["device_name"] or "").lower(),
            )
        )
    except Exception as exc:  # noqa: BLE001 - inventario e melhor-esforco
        device_error = str(exc)

    payload = {
        "store": store,
        "devices": devices,
        "summary": _build_summary(store, devices, today),
        "old_after_days": OLD_DRIVER_DAYS,
    }
    if store_error:
        payload["store_error"] = store_error
    if device_error:
        payload["device_error"] = device_error
    return payload


def _build_summary(store: list[dict], devices: list[dict], today: dt.date) -> dict:
    return {
        "device_count": len(devices),
        "unsigned_count": sum(1 for d in devices if d["status"] == "UNSIGNED"),
        "unknown_sign_count": sum(
            1 for d in devices if d["status"] == "UNKNOWN_SIGN"
        ),
        "old_count": sum(1 for d in devices if d["status"] == "OLD"),
        "store_count": len(store),
        "store_unsigned_count": sum(1 for e in store if e["unsigned"]),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "collected_for_date": today.isoformat(),
    }
