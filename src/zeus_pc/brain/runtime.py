"""Local inference runtime for the Zeus brain (increment 018).

Talks only to a loopback llama-server (OpenAI-compatible API). Detects
the server binary, reports RAM budgets and probes /health. No shell
strings, no remote hosts: every URL must be 127.0.0.1/localhost.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..app_paths import data_dir
from .registry import BrainError

# Modelos ficam em data/models — resolvido pelo app_paths (frozen-aware),
# ou por ZEUS_MODELS_DIR.
DEFAULT_BASE_URL = os.environ.get(
    "ZEUS_BRAIN_URL",
    "http://127.0.0.1:8081",
)
MAX_PROMPT_CHARS = 12000
MAX_TOKENS_LIMIT = 2048
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def models_dir() -> Path:
    override = os.environ.get("ZEUS_MODELS_DIR")
    return Path(override) if override else data_dir() / "models"


def model_path(filename: str, directory: Path | None = None) -> Path:
    base = directory if directory is not None else models_dir()
    # filename is validated by the registry: plain .gguf name, no separators.
    return base / str(filename)


def detect_llama_server() -> str | None:
    """Locate the llama-server binary without running anything."""
    override = os.environ.get("ZEUS_LLAMA_SERVER")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return str(candidate)
        return None
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


def _memory_info() -> tuple[float | None, float | None]:
    """(total_gb, available_gb); None when the platform cannot tell."""
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)
            ):
                return None, None
            total = status.ullTotalPhys / (1024**3)
            available = status.ullAvailPhys / (1024**3)
            return round(total, 2), round(available, 2)

        page = os.sysconf("SC_PAGE_SIZE")
        total = page * os.sysconf("SC_PHYS_PAGES") / (1024**3)
        available = page * os.sysconf("SC_AVPHYS_PAGES") / (1024**3)
        return round(total, 2), round(available, 2)
    except (ValueError, OSError, AttributeError):
        return None, None


def total_ram_gb() -> float | None:
    return _memory_info()[0]


def available_ram_gb() -> float | None:
    return _memory_info()[1]


def assert_loopback(url: str) -> str:
    """Fail closed: the brain never talks to a non-loopback host."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned.startswith(("http://", "https://")):
        raise BrainError(f"URL do runtime invalida: {cleaned!r}")
    host_part = cleaned.split("://", 1)[1].split("/", 1)[0]
    host = host_part.rsplit(":", 1)[0]
    if host not in LOOPBACK_HOSTS:
        raise BrainError(
            "Runtime remoto negado: apenas loopback (127.0.0.1/localhost)."
        )
    return cleaned


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    server_path: str | None
    base_url: str
    reachable: bool
    detail: str
    models_dir: str
    total_ram_gb: float | None
    available_ram_gb: float | None

    @property
    def server_available(self) -> bool:
        return self.server_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_path": self.server_path,
            "server_available": self.server_available,
            "base_url": self.base_url,
            "reachable": self.reachable,
            "detail": self.detail,
            "models_dir": self.models_dir,
            "total_ram_gb": self.total_ram_gb,
            "available_ram_gb": self.available_ram_gb,
        }


def probe_server(
    base_url: str = DEFAULT_BASE_URL,
    *,
    timeout: float = 1.5,
) -> dict[str, Any]:
    """GET /health on a loopback llama-server; never raises."""
    url = assert_loopback(f"{base_url.rstrip('/')}/health")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            payload: dict[str, Any] = {"http_status": response.status}
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except json.JSONDecodeError:
                pass
            return {"reachable": True, "detail": "ok", **payload}
    except urllib.error.HTTPError as exc:
        # llama-server answers 503 while loading a model: still reachable.
        return {"reachable": True, "detail": f"http_{exc.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "reachable": False,
            "detail": f"sem resposta: {type(exc).__name__}",
        }


def runtime_status(base_url: str = DEFAULT_BASE_URL) -> RuntimeStatus:
    probe = probe_server(base_url)
    detail = str(probe.get("detail") or "")
    return RuntimeStatus(
        server_path=detect_llama_server(),
        base_url=assert_loopback(base_url),
        reachable=bool(probe.get("reachable")),
        detail=detail,
        models_dir=str(models_dir()),
        total_ram_gb=total_ram_gb(),
        available_ram_gb=available_ram_gb(),
    )


def chat_complete(
    prompt: str,
    *,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Single-turn completion against the loopback OpenAI-compatible API."""
    url = assert_loopback(f"{base_url.rstrip('/')}/v1/chat/completions")

    text = str(prompt or "")
    if not text.strip():
        raise BrainError("prompt vazio.")
    if len(text) > MAX_PROMPT_CHARS:
        raise BrainError(
            f"prompt excede {MAX_PROMPT_CHARS} caracteres."
        )
    bounded_tokens = min(max(int(max_tokens), 1), MAX_TOKENS_LIMIT)
    bounded_temp = min(max(float(temperature), 0.0), 2.0)

    body = json.dumps(
        {
            "model": str(model or "zeus"),
            "messages": [{"role": "user", "content": text}],
            "max_tokens": bounded_tokens,
            "temperature": bounded_temp,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise BrainError(f"runtime respondeu {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise BrainError(
            f"runtime indisponivel: {type(exc).__name__}"
        ) from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BrainError("resposta do runtime sem conteudo esperado.") from exc

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "content": str(content),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }
