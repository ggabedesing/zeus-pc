from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .models import Metric, ProbeResult, Status, humanize_probe_error
from .powershell import run, run_json


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


def physical_storage_probe() -> ProbeResult:
    blocked = _blocked("Physical Storage")
    if blocked:
        return blocked

    source = "Get-PhysicalDisk"
    try:
        disks = _as_list(
            run_json(
                "Get-PhysicalDisk | "
                "Select-Object FriendlyName,SerialNumber,MediaType,BusType,Size,HealthStatus,OperationalStatus,SpindleSpeed"
            )
        )
        unhealthy = sum(
            1
            for item in disks
            if str(item.get("HealthStatus") or "").lower() not in {"healthy", ""}
        )
        return ProbeResult(
            "Physical Storage",
            Status.OK if disks else Status.PARTIAL,
            [
                Metric("disk_count", len(disks), source=source),
                Metric("unhealthy_count", unhealthy, source=source),
                Metric("disks", disks, source=source),
            ],
            message="" if disks else "Nenhum disco fisico foi retornado pela camada Storage.",
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Physical Storage",
            status,
            message=message,
            source=source,
        )


def storage_reliability_probe() -> ProbeResult:
    blocked = _blocked("Storage Reliability")
    if blocked:
        return blocked

    source = "Get-StorageReliabilityCounter"
    script = (
        "$items=@(); "
        "Get-PhysicalDisk | ForEach-Object { "
        "$d=$_; $r=$null; $counterError=$null; "
        "try { $r=Get-StorageReliabilityCounter -PhysicalDisk $d -ErrorAction Stop } "
        "catch { $counterError=$_.Exception.Message }; "
        "$items += [PSCustomObject]@{"
        "FriendlyName=$d.FriendlyName;"
        "SerialNumber=$d.SerialNumber;"
        "HealthStatus=$d.HealthStatus;"
        "MediaType=$d.MediaType;"
        "ReliabilityAvailable=($null -ne $r);"
        "Temperature=$(if($r){$r.Temperature}else{$null});"
        "Wear=$(if($r){$r.Wear}else{$null});"
        "PowerOnHours=$(if($r){$r.PowerOnHours}else{$null});"
        "ReadErrorsTotal=$(if($r){$r.ReadErrorsTotal}else{$null});"
        "WriteErrorsTotal=$(if($r){$r.WriteErrorsTotal}else{$null});"
        "ReliabilityError=$counterError"
        "} }; $items"
    )
    try:
        disks = _as_list(run_json(script, timeout=12))
        available_count = sum(
            1 for item in disks if item.get("ReliabilityAvailable") is True
        )
        status = Status.OK if disks and available_count else Status.PARTIAL
        message = ""
        if not disks:
            message = "Nenhum disco fisico foi retornado."
        elif not available_count:
            message = "O Windows nao expos contadores de confiabilidade para os discos detectados."
        return ProbeResult(
            "Storage Reliability",
            status,
            [
                Metric("disk_count", len(disks), source=source),
                Metric("available_count", available_count, source=source),
                Metric("disks", disks, source=source),
            ],
            message=message,
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Storage Reliability",
            status,
            message=message,
            source=source,
        )


def trim_probe() -> ProbeResult:
    blocked = _blocked("TRIM")
    if blocked:
        return blocked

    source = "fsutil behavior query DisableDeleteNotify"
    try:
        code, out, err = run("fsutil behavior query DisableDeleteNotify", timeout=5)
        if code != 0:
            raise RuntimeError(err or out or f"fsutil retornou {code}")

        states: dict[str, bool] = {}
        for filesystem, value in re.findall(
            r"(?im)^\s*(NTFS|ReFS)\s+DisableDeleteNotify\s*=\s*([01])",
            out,
        ):
            states[filesystem.upper()] = value == "0"

        if not states:
            return ProbeResult(
                "TRIM",
                Status.PARTIAL,
                [Metric("raw", out, source=source)],
                message="A consulta respondeu, mas o estado NTFS/ReFS nao pôde ser normalizado.",
                source=source,
            )

        return ProbeResult(
            "TRIM",
            Status.OK,
            [
                Metric("ntfs_enabled", states.get("NTFS"), source=source),
                Metric("refs_enabled", states.get("REFS"), source=source),
                Metric("states", states, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, "fsutil")
        return ProbeResult(
            "TRIM",
            status,
            message=message,
            source=source,
        )


def windows_event_summary_probe() -> ProbeResult:
    blocked = _blocked("Windows Events")
    if blocked:
        return blocked

    source = "Get-WinEvent"
    script = (
        "$since=(Get-Date).AddHours(-24); "
        "Get-WinEvent -FilterHashtable @{"
        "LogName=@('System','Application');Level=1,2;StartTime=$since"
        "} -MaxEvents 100 -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated,LogName,Id,ProviderName,Level"
    )
    try:
        events = _as_list(run_json(script, timeout=12))
        critical_count = sum(1 for item in events if int(item.get("Level") or 0) == 1)
        error_count = sum(1 for item in events if int(item.get("Level") or 0) == 2)
        providers = Counter(
            str(item.get("ProviderName") or "UNKNOWN") for item in events
        )
        top_providers = [
            {"provider": name, "count": count}
            for name, count in providers.most_common(10)
        ]
        return ProbeResult(
            "Windows Events",
            Status.OK,
            [
                Metric("window_hours", 24, "hours", "policy"),
                Metric("event_count", len(events), source=source),
                Metric("critical_count", critical_count, source=source),
                Metric("error_count", error_count, source=source),
                Metric("top_providers", top_providers, source=source),
                Metric("events", events, source=source),
            ],
            message="Resumo limitado a 100 eventos Critical/Error das ultimas 24 horas.",
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Windows Events",
            status,
            message=message,
            source=source,
        )
