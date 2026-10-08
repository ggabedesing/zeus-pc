from __future__ import annotations

import ctypes
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


@dataclass(slots=True)
class ProcessPoint:
    pid: int
    name: str
    cpu_pct: float | None
    working_set_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProcessSnapshot:
    timestamp: float
    total_processes: int
    sampled_processes: int
    inaccessible_count: int
    top_cpu: list[ProcessPoint]
    top_memory: list[ProcessPoint]
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["top_cpu"] = [item.to_dict() for item in self.top_cpu]
        payload["top_memory"] = [item.to_dict() for item in self.top_memory]
        return payload


def _filetime_to_int(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _process_cpu_pct(
    previous: tuple[int, int] | None,
    current: tuple[int, int],
    elapsed_seconds: float,
    logical_processors: int,
) -> float | None:
    if previous is None or elapsed_seconds <= 0:
        return None
    previous_creation, previous_total = previous
    current_creation, current_total = current
    if current_creation != previous_creation:
        return None
    delta = current_total - previous_total
    if delta < 0:
        return None
    denominator = (
        elapsed_seconds
        * 10_000_000.0
        * max(int(logical_processors), 1)
    )
    if denominator <= 0:
        return None
    value = (delta / denominator) * 100.0
    return round(min(max(value, 0.0), 100.0), 2)


def _top_points(
    points: list[ProcessPoint],
    field: str,
    limit: int = 10,
) -> list[ProcessPoint]:
    if field == "cpu_pct":
        available = [item for item in points if item.cpu_pct is not None]
        return sorted(
            available,
            key=lambda item: item.cpu_pct or 0.0,
            reverse=True,
        )[:limit]
    if field == "working_set_bytes":
        available = [
            item for item in points if item.working_set_bytes is not None
        ]
        return sorted(
            available,
            key=lambda item: item.working_set_bytes or 0,
            reverse=True,
        )[:limit]
    raise ValueError(f"unsupported process field: {field}")


class NativeProcessSampler:
    def __init__(self, top_n: int = 10) -> None:
        self.top_n = min(max(int(top_n), 1), 25)
        self._previous: dict[int, tuple[int, int]] = {}
        self._last_monotonic: float | None = None
        self._lock = threading.Lock()

    def _apis(self):
        if os.name != "nt":
            return None

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        kernel32.CreateToolhelp32Snapshot.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.Process32FirstW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        kernel32.Process32FirstW.restype = ctypes.c_int
        kernel32.Process32NextW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        kernel32.Process32NextW.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
            ctypes.c_uint32,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        return kernel32, psapi

    def _enumerate(self, kernel32) -> list[tuple[int, str]]:
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPPROCESS,
            0,
        )
        if not snapshot or snapshot == INVALID_HANDLE_VALUE:
            return []

        items: list[tuple[int, str]] = []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(
                snapshot,
                ctypes.byref(entry),
            )
            while ok:
                items.append(
                    (
                        int(entry.th32ProcessID),
                        str(entry.szExeFile),
                    )
                )
                ok = kernel32.Process32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )
        finally:
            kernel32.CloseHandle(snapshot)
        return items

    def _read_process(
        self,
        kernel32,
        psapi,
        pid: int,
        name: str,
    ) -> tuple[ProcessPoint, tuple[int, int]] | None:
        if pid == 0:
            return None

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            0,
            pid,
        )
        if not handle:
            return None

        try:
            creation = _FILETIME()
            exit_time = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None

            creation_value = _filetime_to_int(creation)
            total_time = (
                _filetime_to_int(kernel)
                + _filetime_to_int(user)
            )

            counters = _PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS_EX)
            working_set: int | None = None
            if psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            ):
                working_set = int(counters.WorkingSetSize)

            point = ProcessPoint(
                pid=pid,
                name=name or f"PID {pid}",
                cpu_pct=None,
                working_set_bytes=working_set,
            )
            return point, (creation_value, total_time)
        finally:
            kernel32.CloseHandle(handle)

    def collect(self) -> ProcessSnapshot:
        now = time.time()
        monotonic_now = time.monotonic()
        if os.name != "nt":
            return ProcessSnapshot(
                timestamp=now,
                total_processes=0,
                sampled_processes=0,
                inaccessible_count=0,
                top_cpu=[],
                top_memory=[],
            )

        apis = self._apis()
        if apis is None:
            return ProcessSnapshot(now, 0, 0, 0, [], [])

        kernel32, psapi = apis
        entries = self._enumerate(kernel32)
        logical_processors = os.cpu_count() or 1

        with self._lock:
            elapsed = (
                monotonic_now - self._last_monotonic
                if self._last_monotonic is not None
                else 0.0
            )
            previous = self._previous
            current_map: dict[int, tuple[int, int]] = {}
            points: list[ProcessPoint] = []

            for pid, name in entries:
                result = self._read_process(
                    kernel32,
                    psapi,
                    pid,
                    name,
                )
                if result is None:
                    continue
                point, current = result
                point.cpu_pct = _process_cpu_pct(
                    previous.get(pid),
                    current,
                    elapsed,
                    logical_processors,
                )
                current_map[pid] = current
                points.append(point)

            self._previous = current_map
            self._last_monotonic = monotonic_now

        return ProcessSnapshot(
            timestamp=now,
            total_processes=len(entries),
            sampled_processes=len(points),
            inaccessible_count=max(len(entries) - len(points), 0),
            top_cpu=_top_points(points, "cpu_pct", self.top_n),
            top_memory=_top_points(
                points,
                "working_set_bytes",
                self.top_n,
            ),
        )


class ProcessObserverStore:
    def __init__(self, max_snapshots: int = 40) -> None:
        self.max_snapshots = max(int(max_snapshots), 5)
        self._items: deque[ProcessSnapshot] = deque(
            maxlen=self.max_snapshots
        )
        self._lock = threading.Lock()

    def record(self, snapshot: ProcessSnapshot) -> ProcessSnapshot:
        with self._lock:
            self._items.append(snapshot)
        return snapshot

    def latest(
        self,
        session_id: str | None = None,
    ) -> ProcessSnapshot | None:
        items = self.recent(self.max_snapshots, session_id=session_id)
        return items[-1] if items else None

    def recent(
        self,
        limit: int = 10,
        session_id: str | None = None,
    ) -> list[ProcessSnapshot]:
        safe_limit = min(
            max(int(limit), 1),
            self.max_snapshots,
        )
        with self._lock:
            items = list(self._items)
        if session_id:
            items = [
                item for item in items if item.session_id == session_id
            ]
        return items[-safe_limit:]


_PROCESS_SAMPLER = NativeProcessSampler()
PROCESS_OBSERVER = ProcessObserverStore()


def collect_process_snapshot() -> ProcessSnapshot:
    return _PROCESS_SAMPLER.collect()
