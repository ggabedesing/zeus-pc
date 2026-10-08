from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Callable

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SESSION_MODES = {
    "AUTO",
    "GENERAL",
    "GAMING",
    "STREAMING",
    "GAMING_STREAMING",
    "WORK",
    "EDITING",
    "PROGRAMMING",
}


@dataclass(slots=True)
class Sample:
    timestamp: float
    cpu_pct: float | None
    ram_used_pct: float | None
    gpu_pct: float | None
    gpu_temp_c: float | None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ObserverSession:
    id: str
    label: str
    mode: str
    started_at: float
    ended_at: float | None = None

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


def adaptive_interval_seconds(sample: Sample) -> float:
    utilization = [
        value
        for value in (sample.cpu_pct, sample.gpu_pct)
        if value is not None
    ]
    if not utilization:
        return 2.0
    peak = max(utilization)
    if peak >= 60.0:
        return 1.0
    if peak >= 25.0:
        return 2.0
    return 5.0


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

    def recent(
        self,
        limit: int = 60,
        session_id: str | None = None,
    ) -> list[Sample]:
        safe_limit = min(max(int(limit), 1), self.max_samples)
        with self._lock:
            samples = list(self._samples)
        if session_id:
            samples = [
                item for item in samples if item.session_id == session_id
            ]
        return samples[-safe_limit:]

    def baseline(
        self,
        limit: int = 60,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        samples = self.recent(limit, session_id=session_id)
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
            "session_id": session_id,
            "sample_count": len(samples),
            "duration_seconds": round(duration, 3),
            "metrics": summaries,
        }


class ObserverRunner:
    def __init__(
        self,
        store: ObserverStore,
        provider: Callable[[], Sample] = collect_sample,
        session_history: int = 20,
    ) -> None:
        self.store = store
        self._provider = provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._current_interval = 2.0
        self._last_sample_time: float | None = None
        self._active_session: ObserverSession | None = None
        self._sessions: deque[ObserverSession] = deque(
            maxlen=max(int(session_history), 1)
        )

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> bool:
        with self._lock:
            if self.is_running():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ZeusObserver",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 3.0) -> bool:
        with self._lock:
            thread = self._thread
            if not thread or not thread.is_alive():
                self._close_session_locked()
                return False
            self._stop_event.set()

        thread.join(timeout=max(float(timeout), 0.0))

        with self._lock:
            stopped = not thread.is_alive()
            if stopped:
                self._thread = None
                self._close_session_locked()
            return stopped

    def _run(self) -> None:
        while not self._stop_event.is_set():
            sample = self.sample_once()
            interval = adaptive_interval_seconds(sample)
            with self._lock:
                self._current_interval = interval
            if self._stop_event.wait(interval):
                break

    def sample_once(self) -> Sample:
        sample = self._provider()
        with self._lock:
            if self._active_session is not None:
                sample.session_id = self._active_session.id
            self._last_sample_time = sample.timestamp
        self.store.record(sample)
        return sample

    def start_session(
        self,
        label: str = "",
        mode: str = "AUTO",
    ) -> tuple[ObserverSession, bool]:
        normalized_mode = str(mode or "AUTO").upper().strip()
        if normalized_mode not in SESSION_MODES:
            normalized_mode = "AUTO"
        normalized_label = str(label or "").strip()[:80]
        if not normalized_label:
            normalized_label = normalized_mode.replace("_", " ").title()

        with self._lock:
            if self._active_session is not None:
                return self._active_session, False
            session = ObserverSession(
                id=str(uuid.uuid4()),
                label=normalized_label,
                mode=normalized_mode,
                started_at=time.time(),
            )
            self._active_session = session
            return session, True

    def stop_session(self) -> ObserverSession | None:
        with self._lock:
            return self._close_session_locked()

    def _close_session_locked(self) -> ObserverSession | None:
        session = self._active_session
        if session is None:
            return None
        if session.ended_at is None:
            session.ended_at = time.time()
        self._sessions.append(session)
        self._active_session = None
        return session

    def recent_sessions(self, limit: int = 10) -> list[ObserverSession]:
        safe_limit = min(max(int(limit), 1), self._sessions.maxlen or 20)
        with self._lock:
            return list(self._sessions)[-safe_limit:]

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = (
                self._active_session.to_dict()
                if self._active_session is not None
                else None
            )
            last_session = (
                self._sessions[-1].to_dict()
                if self._sessions
                else None
            )
            return {
                "running": self.is_running(),
                "current_interval_seconds": self._current_interval,
                "last_sample_time": self._last_sample_time,
                "active_session": active,
                "last_session": last_session,
            }


OBSERVER = ObserverStore()
OBSERVER_RUNNER = ObserverRunner(OBSERVER)
