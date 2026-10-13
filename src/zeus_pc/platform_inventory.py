from __future__ import annotations

from typing import Any

from .models import Metric, ProbeResult, Status, demote_if_unknown, humanize_probe_error
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


def tpm_probe() -> ProbeResult:
    blocked = _blocked("TPM")
    if blocked:
        return blocked

    source = "Get-Tpm"
    try:
        data = run_json(
            "Get-Tpm | "
            "Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated,ManagedAuthLevel,OwnerClearDisabled,AutoProvisioning"
        )
        if not isinstance(data, dict):
            data = {}
        return demote_if_unknown(
            ProbeResult(
                "TPM",
                Status.OK,
                [
                    Metric("present", data.get("TpmPresent"), source=source),
                    Metric("ready", data.get("TpmReady"), source=source),
                    Metric("enabled", data.get("TpmEnabled"), source=source),
                    Metric("activated", data.get("TpmActivated"), source=source),
                    Metric(
                        "auto_provisioning",
                        data.get("AutoProvisioning"),
                        source=source,
                    ),
                    Metric("details", data, source=source),
                ],
                source=source,
            ),
            {"present", "ready", "enabled", "activated", "auto_provisioning"},
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "TPM",
            status,
            message=message,
            source=source,
        )


def firewall_probe() -> ProbeResult:
    blocked = _blocked("Firewall")
    if blocked:
        return blocked

    source = "Get-NetFirewallProfile"
    try:
        profiles = _as_list(
            run_json(
                "Get-NetFirewallProfile | "
                "Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction,NotifyOnListen,AllowLocalFirewallRules"
            )
        )
        enabled_count = sum(1 for item in profiles if item.get("Enabled") is True)
        all_enabled = bool(profiles) and enabled_count == len(profiles)
        return demote_if_unknown(
            ProbeResult(
                "Firewall",
                Status.OK,
                [
                    Metric("profile_count", len(profiles), source=source),
                    Metric("enabled_count", enabled_count, source=source),
                    Metric("all_profiles_enabled", all_enabled, source=source),
                    Metric("profiles", profiles, source=source),
                ],
                source=source,
            ),
            {"profiles"},
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Firewall",
            status,
            message=message,
            source=source,
        )


def proxy_probe() -> ProbeResult:
    blocked = _blocked("Proxy")
    if blocked:
        return blocked

    source = "HKCU Internet Settings"
    try:
        data = run_json(
            "Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' | "
            "Select-Object ProxyEnable,ProxyServer,AutoConfigURL,AutoDetect"
        )
        if not isinstance(data, dict):
            data = {}
        enabled_raw = data.get("ProxyEnable")
        enabled = bool(int(enabled_raw or 0)) if str(enabled_raw or "0").isdigit() else bool(enabled_raw)
        return demote_if_unknown(
            ProbeResult(
                "Proxy",
                Status.OK,
                [
                    Metric("enabled", enabled, source=source),
                    Metric("server", data.get("ProxyServer"), source=source),
                    Metric("auto_config_url", data.get("AutoConfigURL"), source=source),
                    Metric("auto_detect", data.get("AutoDetect"), source=source),
                    Metric("scope", "CURRENT_USER", source="policy"),
                ],
                message="Leitura limitada ao proxy do usuario atual; WinHTTP pode ter estado separado.",
                source=source,
            ),
            {"enabled", "server", "auto_config_url", "auto_detect"},
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Proxy",
            status,
            message=message,
            source=source,
        )


def network_link_probe() -> ProbeResult:
    blocked = _blocked("Network Link")
    if blocked:
        return blocked

    source = "Get-NetAdapter"
    try:
        adapters = _as_list(
            run_json(
                "Get-NetAdapter | "
                "Select-Object Name,InterfaceDescription,Status,LinkSpeed,MacAddress,DriverDescription,DriverVersion,DriverDate,MediaType,PhysicalMediaType"
            )
        )
        up_count = sum(
            1 for item in adapters if str(item.get("Status") or "").lower() == "up"
        )
        return ProbeResult(
            "Network Link",
            Status.OK,
            [
                Metric("adapter_count", len(adapters), source=source),
                Metric("up_count", up_count, source=source),
                Metric("adapters", adapters, source=source),
            ],
            source=source,
        )
    except Exception as exc:
        status, message = humanize_probe_error(exc, source)
        return ProbeResult(
            "Network Link",
            status,
            message=message,
            source=source,
        )
