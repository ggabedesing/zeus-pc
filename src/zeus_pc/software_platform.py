from __future__ import annotations

from typing import Any

from .models import (
    Metric,
    ProbeResult,
    Status,
    demote_if_unknown,
    humanize_probe_error,
)
from .powershell import run_json

OPTIONAL_FEATURE_STATES = {
    1: "Enabled",
    2: "Disabled",
    3: "Not Present",
}


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


def installed_software_probe() -> ProbeResult:
    blocked = _blocked("Installed Software")
    if blocked:
        return blocked

    source = "Windows uninstall registry"
    script = (
        "$sources=@("
        "[PSCustomObject]@{Path='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Scope='MACHINE_64'},"
        "[PSCustomObject]@{Path='HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Scope='MACHINE_32'},"
        "[PSCustomObject]@{Path='HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Scope='CURRENT_USER'}"
        "); $items=@(); "
        "foreach($source in $sources){ "
        "Get-ItemProperty $source.Path -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName } | ForEach-Object { "
        "$items += [PSCustomObject]@{"
        "DisplayName=$_.DisplayName;"
        "DisplayVersion=$_.DisplayVersion;"
        "Publisher=$_.Publisher;"
        "InstallDate=$_.InstallDate;"
        "InstallLocation=$_.InstallLocation;"
        "Scope=$source.Scope"
        "} } }; $items"
    )
    try:
        raw_items = _as_list(run_json(script, timeout=12))
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in raw_items:
            name = str(item.get("DisplayName") or "").strip()
            if not name:
                continue
            version = str(item.get("DisplayVersion") or "").strip()
            publisher = str(item.get("Publisher") or "").strip()
            key = (name.lower(), version.lower(), publisher.lower())
            scope = str(item.get("Scope") or "UNKNOWN")
            if key not in unique:
                normalized = dict(item)
                normalized.pop("Scope", None)
                normalized["Scopes"] = [scope]
                unique[key] = normalized
            elif scope not in unique[key]["Scopes"]:
                unique[key]["Scopes"].append(scope)

        items = sorted(
            unique.values(),
            key=lambda item: str(item.get("DisplayName") or "").lower(),
        )
        publishers = {
            str(item.get("Publisher") or "").strip().lower()
            for item in items
            if str(item.get("Publisher") or "").strip()
        }
        return ProbeResult(
            "Installed Software",
            Status.OK,
            [
                Metric("count", len(items), source=source),
                Metric("publisher_count", len(publishers), source=source),
                Metric("items", items, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Installed Software",
            status,
            message=message,
            source=source,
        )


def virtualization_probe() -> ProbeResult:
    blocked = _blocked("Virtualization")
    if blocked:
        return blocked

    source = "CIM virtualization context"
    script = (
        "$cs=Get-CimInstance Win32_ComputerSystem; "
        "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$dg=$null; "
        "try { $dg=Get-CimInstance -Namespace 'root\\Microsoft\\Windows\\DeviceGuard' "
        "-ClassName Win32_DeviceGuard -ErrorAction Stop } catch {}; "
        "[PSCustomObject]@{"
        "HypervisorPresent=$cs.HypervisorPresent;"
        "VirtualizationFirmwareEnabled=$cpu.VirtualizationFirmwareEnabled;"
        "SecondLevelAddressTranslationExtensions=$cpu.SecondLevelAddressTranslationExtensions;"
        "VirtualizationBasedSecurityStatus=$(if($dg){$dg.VirtualizationBasedSecurityStatus}else{$null});"
        "SecurityServicesConfigured=$(if($dg){$dg.SecurityServicesConfigured}else{$null});"
        "SecurityServicesRunning=$(if($dg){$dg.SecurityServicesRunning}else{$null})"
        "}"
    )
    try:
        data = run_json(script)
        if not isinstance(data, dict):
            data = {}
        return ProbeResult(
            "Virtualization",
            Status.OK,
            [
                Metric("hypervisor_present", data.get("HypervisorPresent"), source=source),
                Metric(
                    "firmware_virtualization_enabled",
                    data.get("VirtualizationFirmwareEnabled"),
                    source=source,
                ),
                Metric(
                    "slat_supported",
                    data.get("SecondLevelAddressTranslationExtensions"),
                    source=source,
                ),
                Metric(
                    "vbs_status",
                    data.get("VirtualizationBasedSecurityStatus"),
                    source=source,
                ),
                Metric(
                    "security_services_configured",
                    data.get("SecurityServicesConfigured"),
                    source=source,
                ),
                Metric(
                    "security_services_running",
                    data.get("SecurityServicesRunning"),
                    source=source,
                ),
            ],
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Virtualization",
            status,
            message=message,
            source=source,
        )


def optional_features_probe() -> ProbeResult:
    blocked = _blocked("Windows Features")
    if blocked:
        return blocked

    source = "Win32_OptionalFeature"
    try:
        rows = _as_list(
            run_json(
                "Get-CimInstance Win32_OptionalFeature | "
                "Select-Object Name,Caption,InstallState",
                timeout=20,
            )
        )
        items = [
            {
                "name": item.get("Name"),
                "caption": item.get("Caption"),
                "state": OPTIONAL_FEATURE_STATES.get(
                    item.get("InstallState"),
                    "Unknown",
                ),
            }
            for item in rows
        ]
        enabled_count = sum(1 for item in items if item["state"] == "Enabled")
        return demote_if_unknown(
            ProbeResult(
                "Windows Features",
                Status.OK,
                [
                    Metric("count", len(items), source=source),
                    Metric("enabled_count", enabled_count, source=source),
                    Metric("items", items, source=source),
                ],
                source=source,
            ),
            meaningful=("count", "enabled_count"),
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source, default_status=Status.PARTIAL)
        return ProbeResult(
            "Windows Features",
            status,
            message=message,
            source=source,
        )
