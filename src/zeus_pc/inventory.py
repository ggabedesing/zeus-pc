from __future__ import annotations

from typing import Any

from .models import Metric, ProbeResult, Status
from .powershell import run_json


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _blocked(domain: str) -> ProbeResult | None:
    import os

    if os.name != "nt":
        return ProbeResult(
            domain,
            Status.UNSUPPORTED,
            message="Disponivel apenas no Windows.",
        )
    return None


def services_probe() -> ProbeResult:
    blocked = _blocked("Services")
    if blocked:
        return blocked

    source = "Win32_Service"
    try:
        services = _as_list(
            run_json(
                "Get-CimInstance Win32_Service | "
                "Select-Object Name,DisplayName,State,StartMode,StartName,ProcessId,PathName"
            )
        )
        running_count = sum(
            1 for item in services if str(item.get("State") or "").lower() == "running"
        )
        automatic_count = sum(
            1
            for item in services
            if str(item.get("StartMode") or "").lower() in {"auto", "automatic"}
        )
        return ProbeResult(
            "Services",
            Status.OK,
            [
                Metric("count", len(services), source=source),
                Metric("running_count", running_count, source=source),
                Metric("automatic_count", automatic_count, source=source),
                Metric("items", services, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        return ProbeResult("Services", Status.FAILED, message=str(exc), source=source)


def scheduled_tasks_probe() -> ProbeResult:
    blocked = _blocked("Scheduled Tasks")
    if blocked:
        return blocked

    source = "Get-ScheduledTask"
    try:
        tasks = _as_list(
            run_json(
                "Get-ScheduledTask | "
                "Select-Object TaskPath,TaskName,State"
            )
        )
        disabled_count = sum(
            1 for item in tasks if str(item.get("State") or "").lower() == "disabled"
        )
        return ProbeResult(
            "Scheduled Tasks",
            Status.OK,
            [
                Metric("count", len(tasks), source=source),
                Metric("disabled_count", disabled_count, source=source),
                Metric("items", tasks, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        return ProbeResult(
            "Scheduled Tasks",
            Status.FAILED,
            message=str(exc),
            source=source,
        )


def processes_probe() -> ProbeResult:
    blocked = _blocked("Processes")
    if blocked:
        return blocked

    source = "Win32_Process"
    try:
        processes = _as_list(
            run_json(
                "Get-CimInstance Win32_Process | "
                "Select-Object Name,ProcessId,ParentProcessId,WorkingSetSize,ExecutablePath,ThreadCount,HandleCount"
            )
        )

        def working_set(item: dict[str, Any]) -> int:
            try:
                return int(item.get("WorkingSetSize") or 0)
            except (TypeError, ValueError):
                return 0

        total_working_set = sum(working_set(item) for item in processes)
        top_memory = sorted(
            processes,
            key=working_set,
            reverse=True,
        )[:15]

        return ProbeResult(
            "Processes",
            Status.OK,
            [
                Metric("count", len(processes), source=source),
                Metric(
                    "total_working_set_bytes",
                    total_working_set,
                    "bytes",
                    source,
                ),
                Metric("top_memory", top_memory, source=source),
                Metric("items", processes, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        return ProbeResult("Processes", Status.FAILED, message=str(exc), source=source)
