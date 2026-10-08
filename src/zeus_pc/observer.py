from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Callable

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(slots=True)
class Sample:
    timestamp: float
    cpu_pct: float | None
    ram_used_pct: float | None
    gpu_pct: float | None
    gpu_temp_c: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _filetime_to_int(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _cpu_usage_from_times(
    previous: tuple[int, int, int] | None,
    current: tuple[int, int, int],
) -> float | None:
    if previous is None:
        return None

    idle_delta = current[0] - previous[0]
    kernel_delta = current[1] - previous[1]
    user_delta = current[2] - previous[2]
    total_delta = kernel_delta + user_delta
    if total_delta <= 0 or idle_delta < 0:
        return None

    busy = 1.0 - (idle_delta / total_delta)
    return round(min(max(busy * 100.0, 0.0), 100.0), 1)


class NativeWindowsSampler:
    def __init__(self) -> None:
        self._last_cpu_times: tuple[int, int, int] | None = None
        self._lock = threading.Lock()

    def _read_cpu_times(self) -> tuple[int, int, int] | None:
        if os.name != "nt":
            return None

        idle = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        try:
            ok = ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
        except Exception:
            return None
        if not ok:
            return None

        return (
            _filetime_to_int(idle),
            _filetime_to_int(kernel),
            _filetime_to_int(user),
        )

    def cpu_pct(self) -> float | None:
        current = self._read_cpu_times()
        if current is None:
            return None

        with self._lock:
            value = _cpu_usage_from_times(self._last_cpu_times, current)
            self._last_cpu_times = current
        return value

    def ram_used_pct(self) -> float | None:
        if os.name != "nt":
            return None

        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        try:
            ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        except Exception:
            return None
        if not ok:
            return None
        return float(status.dwMemoryLoad)


@lru_cache(maxsize=1)
def _nvidia_smi_path() -> str | None:
    return shutil.which("nvidia-smi")


def _gpu_sample() -> tuple[float | None, float | None]:
    if os.name != "nt":
        return None, None

    smi = _nvidia_smi_path()
    if not smi:
        return None, None

    try:
        proc = subprocess.run(
            [
                smi,
                "--query-gpu=utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None, None
        parts = [
            part.strip()
            for part in proc.stdout.strip().splitlines()[0].split(",")
        ]
        if len(parts) < 2:
            return None, None
        return float(parts[0]), float(parts[1])
    except Exception:
        return None, None


_NATIVE = NativeWindowsSampler()


def collect_sample() -> Sample:
    gpu, temperature = _gpu_sample()
    return Sample(
        timestamp=time.time(),
        cpu_pct=_NATIVE.cpu_pct(),
        ram_used_pct=_NATIVE.ram_used_pct(),
        gpu_pct=gpu,
        gpu_temp_c=temperature,
    )


class ObserverStore:
    def __init__(
        self,
        max_samples: int = 300,
        provider: Callable[[], Sample] = collect_sample,
    ) -> None:
        self.max_samples = max(int(max_samples), 10)
        self._samples: deque[Sample] = deque(maxlen=self.max_samples)
        self._provider = provider
        self._lock = threading.Lock()

    def record(self, sample: Sample | None = None) -> Sample:
        value = sample or self._provider()
        with self._lock:
            self._samples.append(value)
        return value

    def recent(self, limit: int = 60) -> list[Sample]:
        safe_limit = min(max(int(limit), 1), self.max_samples)
        with self._lock:
            return list(self._samples)[-safe_limit:]

    def baseline(self, limit: int = 60) -> dict[str, Any]:
        samples = self.recent(limit)
        metrics = {
            "cpu_pct": [item.cpu_pct for item in samples if item.cpu_pct is not None],
            "ram_used_pct": [
                item.ram_used_pct
                for item in samples
                if item.ram_used_pct is not None
            ],
            "gpu_pct": [item.gpu_pct for item in samples if item.gpu_pct is not None],
            "gpu_temp_c": [
                item.gpu_temp_c
                for item in samples
                if item.gpu_temp_c is not None
            ],
        }

        summaries: dict[str, dict[str, float | int] | None] = {}
        for name, values in metrics.items():
            if not values:
                summaries[name] = None
                continue
            summaries[name] = {
                "count": len(values),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
            }

        duration = 0.0
        if len(samples) >= 2:
            duration = max(samples[-1].timestamp - samples[0].timestamp, 0.0)

        return {
            "sample_count": len(samples),
            "duration_seconds": round(duration, 3),
            "metrics": summaries,
        }


OBSERVER = ObserverStore()
