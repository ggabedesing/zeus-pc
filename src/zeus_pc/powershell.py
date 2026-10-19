from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _oem_encoding() -> str | None:
    """Windows console OEM codepage (e.g. cp850 on pt-BR)."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        codepage = ctypes.windll.kernel32.GetOEMCP()
    except Exception:
        return None
    return f"cp{codepage}" if codepage else None


def _decode_output(data: bytes) -> str:
    """Decode PowerShell output honestly.

    Windows PowerShell writes to a redirected pipe using the console OEM
    codepage (cp850 on pt-BR: 'a' with acute is 0xA0), while
    ``text=True`` would read it as the ANSI codepage (cp1252, where
    0xA0 is a non-breaking space) — silently corrupting accented
    names. UTF-8 is tried first (cp850 accented bytes are invalid
    UTF-8, so real cp850 output falls through to the OEM candidate).
    """
    if not data:
        return ""
    candidates = ["utf-8-sig"]
    oem = _oem_encoding()
    if oem:
        candidates.append(oem)
    candidates.extend(("cp1252", "latin-1"))
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")


def run(script: str, timeout: int = 8) -> tuple[int, str, str]:
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not exe:
        return 127, "", "PowerShell indisponivel"
    proc = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return (
        proc.returncode,
        _decode_output(proc.stdout).strip(),
        _decode_output(proc.stderr).strip(),
    )


def run_json(script: str, timeout: int = 8) -> Any:
    code, out, err = run(f"{script} | ConvertTo-Json -Depth 7 -Compress", timeout)
    if code != 0:
        raise RuntimeError(err or f"PowerShell retornou {code}")
    return json.loads(out) if out else None
