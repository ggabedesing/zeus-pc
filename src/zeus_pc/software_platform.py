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


def _error_status(exc: Exception | str) -> Status:
    text = str(exc).lower()
    if any(
        token in text
        for token in (
            "access is denied",
            "acesso negado",
            "requires elevation",
            "administrator",
            "administrador",
        )
    ):
        return Status.REQUIRES_ELEVATION
    if any(
        token in text
        for token in (
            "is not recognized",
            "não é reconhecido",
            "nao e reconhecido",
            "commandnotfound",
            "invalid namespace",
            "namespace inexistente",
        )
    ):
        return Status.UNSUPPORTED
    return Status.FAILED


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
        return ProbeResult(
            "Installed Software",
            _error_status(exc),
            message=str(exc),
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
        return ProbeResult(
            "Virtualization",
            _error_status(exc),
            message=str(exc),
            source=source,
        )


def optional_features_probe() -> ProbeResult:
    blocked = _blocked("Windows Features")
    if blocked:
        return blocked

    source = "Get-WindowsOptionalFeature"
    try:
        features = _as_list(
            run_json(
                "Get-WindowsOptionalFeature -Online | "
                "Select-Object FeatureName,State",
                timeout=15,
            )
        )
        enabled_count = sum(
            1 for item in features if str(item.get("State") or "").lower() == "enabled"
        )
        return ProbeResult(
            "Windows Features",
            Status.OK,
            [
                Metric("count", len(features), source=source),
                Metric("enabled_count", enabled_count, source=source),
                Metric("items", features, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        return ProbeResult(
            "Windows Features",
            _error_status(exc),
            message=str(exc),
            source=source,
        )
