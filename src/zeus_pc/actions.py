from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from .powershell import _decode_output

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str


def list_power_plans() -> list[dict[str, str | bool]]:
    if os.name != "nt":
        return []
    proc = subprocess.run(["powercfg", "/list"], capture_output=True, timeout=4, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        return []
    plans: list[dict[str, str | bool]] = []
    for line in _decode_output(proc.stdout).splitlines():
        match = re.search(r"([0-9a-fA-F-]{36})\s*\((.*?)\)\s*(\*)?", line)
        if match:
            plans.append({"guid": match.group(1).lower(), "name": match.group(2).strip(), "active": bool(match.group(3))})
    return plans


def set_power_plan(guid: str) -> ActionResult:
    if not GUID_RE.fullmatch(guid):
        return ActionResult(False, "GUID invalido.")
    known = {item["guid"] for item in list_power_plans()}
    if guid.lower() not in known:
        return ActionResult(False, "Plano de energia nao encontrado.")
    proc = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, timeout=4, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        return ActionResult(False, _decode_output(proc.stderr).strip() or "Falha ao alterar o plano de energia.")
    active = next((item for item in list_power_plans() if item["active"]), None)
    if not active or active["guid"] != guid.lower():
        return ActionResult(False, "A alteracao nao foi confirmada pelo Windows.")
    return ActionResult(True, "Plano de energia alterado e verificado.")
