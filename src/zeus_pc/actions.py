from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str


def decode_console_output(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("oem", "utf-8"):
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def list_power_plans() -> list[dict[str, str | bool]]:
    if os.name != "nt":
        return []
    proc = subprocess.run(
        ["powercfg", "/list"],
        capture_output=True,
        timeout=4,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        return []
    plans: list[dict[str, str | bool]] = []
    for line in decode_console_output(proc.stdout).splitlines():
        match = re.search(
            r"([0-9a-fA-F-]{36})\s*\((.*?)\)\s*(\*)?",
            line,
        )
        if match:
            plans.append(
                {
                    "guid": match.group(1).lower(),
                    "name": match.group(2).strip(),
                    "active": bool(match.group(3)),
                }
            )
    return plans


def apply_power_plan(guid: str) -> ActionResult:
    normalized = str(guid or "").strip().lower()
    if not GUID_RE.fullmatch(normalized):
        return ActionResult(False, "GUID invalido.")

    proc = subprocess.run(
        ["powercfg", "/setactive", normalized],
        capture_output=True,
        timeout=4,
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        return ActionResult(
            False,
            decode_console_output(proc.stderr).strip()
            or decode_console_output(proc.stdout).strip()
            or "Falha ao alterar o plano de energia.",
        )
    return ActionResult(
        True,
        "Comando de alteracao do plano de energia concluido.",
    )


def set_power_plan(guid: str) -> ActionResult:
    normalized = str(guid or "").strip().lower()
    if not GUID_RE.fullmatch(normalized):
        return ActionResult(False, "GUID invalido.")
    known = {str(item["guid"]).lower() for item in list_power_plans()}
    if normalized not in known:
        return ActionResult(False, "Plano de energia nao encontrado.")

    applied = apply_power_plan(normalized)
    if not applied.ok:
        return applied

    active = next(
        (item for item in list_power_plans() if item["active"]),
        None,
    )
    if not active or str(active["guid"]).lower() != normalized:
        return ActionResult(
            False,
            "A alteracao nao foi confirmada pelo Windows.",
        )
    return ActionResult(
        True,
        "Plano de energia alterado e verificado.",
    )
