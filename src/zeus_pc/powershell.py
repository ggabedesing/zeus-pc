from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CONSOLE_ENCODING_PREFIX = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
ECHO_LINE = re.compile(r"^\s*(\+|~|No linha|At line|Position|CategoryInfo|FullyQualifiedErrorId)", re.IGNORECASE)


def _clean_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines()]
    kept = [line for line in lines if line and not ECHO_LINE.match(line)]
    return " ".join(kept).strip()


def run(script: str, timeout: int = 8) -> tuple[int, str, str]:
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not exe:
        return 127, "", "PowerShell indisponivel"
    proc = None
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", CONSOLE_ENCODING_PREFIX + script],
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "Tempo esgotado ao executar a consulta."
    except OSError:
        return 127, "", "Nao foi possivel executar a consulta."
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = _clean_error(proc.stderr.decode("utf-8", errors="replace"))
    if proc.returncode != 0 and not err:
        err = "A consulta ao PowerShell retornou um erro."
    return proc.returncode, out, err


def run_json(script: str, timeout: int = 8) -> Any:
    code, out, err = run(f"{script} | ConvertTo-Json -Depth 7 -Compress", timeout)
    if code != 0:
        raise RuntimeError(err or f"PowerShell retornou {code}")
    return json.loads(out) if out else None
