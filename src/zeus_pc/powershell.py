from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(script: str, timeout: int = 8) -> tuple[int, str, str]:
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not exe:
        return 127, "", "PowerShell indisponivel"
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def run_json(script: str, timeout: int = 8) -> Any:
    code, out, err = run(f"{script} | ConvertTo-Json -Depth 7 -Compress", timeout)
    if code != 0:
        raise RuntimeError(err or f"PowerShell retornou {code}")
    return json.loads(out) if out else None
