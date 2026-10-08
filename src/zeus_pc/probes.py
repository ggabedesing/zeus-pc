from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .inventory import processes_probe, scheduled_tasks_probe, services_probe
from .platform_inventory import firewall_probe, network_link_probe, proxy_probe, tpm_probe
from .models import Metric, ProbeResult, Status
from .powershell import run, run_json

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _windows_only(domain: str) -> ProbeResult | None:
    if os.name != "nt":
        return ProbeResult(domain, Status.UNSUPPORTED, message="Disponivel apenas no Windows.")
    return None


def windows_probe() -> ProbeResult:
    blocked = _windows_only("Windows")
    if blocked:
        return blocked
    metrics = [Metric("system", platform.system(), source="platform"), Metric("architecture", platform.machine(), source="platform")]
    try:
        data = run_json("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,LastBootUpTime")
        metrics += [
            Metric("edition", data.get("Caption"), source="Win32_OperatingSystem"),
            Metric("version", data.get("Version"), source="Win32_OperatingSystem"),
            Metric("build", data.get("BuildNumber"), source="Win32_OperatingSystem"),
            Metric("last_boot", data.get("LastBootUpTime"), source="Win32_OperatingSystem"),
        ]
        return ProbeResult("Windows", Status.OK, metrics, source="CIM")
    except Exception as exc:
        return ProbeResult("Windows", Status.PARTIAL, metrics, str(exc), "platform")


def cpu_probe() -> ProbeResult:
    blocked = _windows_only("CPU")
    if blocked:
        return blocked
    try:
        data = run_json("Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,LoadPercentage")
        return ProbeResult("CPU", Status.OK, [
            Metric("model", data.get("Name"), source="Win32_Processor"),
            Metric("cores", data.get("NumberOfCores"), source="Win32_Processor"),
            Metric("threads", data.get("NumberOfLogicalProcessors"), source="Win32_Processor"),
            Metric("max_clock_mhz", data.get("MaxClockSpeed"), "MHz", "Win32_Processor"),
            Metric("load_pct", data.get("LoadPercentage"), "%", "Win32_Processor"),
        ], source="Win32_Processor")
    except Exception as exc:
        return ProbeResult("CPU", Status.FAILED, message=str(exc), source="Win32_Processor")


def ram_probe() -> ProbeResult:
    blocked = _windows_only("RAM")
    if blocked:
        return blocked
    try:
        os_data = run_json("Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory")
        modules = run_json("Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,Manufacturer,PartNumber")
        if isinstance(modules, dict):
            modules = [modules]
        modules = modules or []
        installed = sum(int(item.get("Capacity") or 0) for item in modules)
        total_kb = int(os_data.get("TotalVisibleMemorySize") or 0)
        free_kb = int(os_data.get("FreePhysicalMemory") or 0)
        used_pct = round((1 - free_kb / total_kb) * 100, 1) if total_kb else None
        return ProbeResult("RAM", Status.OK, [
            Metric("installed_gb", round(installed / (1024**3), 2), "GB", "Win32_PhysicalMemory"),
            Metric("usable_gb", round(total_kb / 1024 / 1024, 2), "GB", "Win32_OperatingSystem"),
            Metric("available_gb", round(free_kb / 1024 / 1024, 2), "GB", "Win32_OperatingSystem"),
            Metric("used_pct", used_pct, "%", "Win32_OperatingSystem"),
            Metric("modules", modules, source="Win32_PhysicalMemory"),
        ], source="CIM")
    except Exception as exc:
        return ProbeResult("RAM", Status.FAILED, message=str(exc), source="CIM")


def gpu_probe() -> ProbeResult:
    blocked = _windows_only("GPU")
    if blocked:
        return blocked
    metrics: list[Metric] = []
    try:
        adapters = run_json("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,DriverDate,Status")
        if isinstance(adapters, dict):
            adapters = [adapters]
        metrics.append(Metric("adapters", adapters or [], source="Win32_VideoController"))
    except Exception as exc:
        return ProbeResult("GPU", Status.FAILED, message=str(exc), source="Win32_VideoController")

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            proc = subprocess.run(
                [smi, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,clocks.gr,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts = [part.strip() for part in proc.stdout.strip().splitlines()[0].split(",")]
                keys = ["name", "memory_total_mib", "memory_used_mib", "utilization_pct", "temperature_c", "graphics_clock_mhz", "driver"]
                metrics.extend(Metric(key, value, source="nvidia-smi") for key, value in zip(keys, parts))
                return ProbeResult("GPU", Status.OK, metrics, source="CIM+nvidia-smi")
        except Exception:
            pass
    return ProbeResult("GPU", Status.PARTIAL, metrics, "Telemetria avancada indisponivel.", "Win32_VideoController")


def storage_probe() -> ProbeResult:
    blocked = _windows_only("Storage")
    if blocked:
        return blocked
    volumes = []
    for root in [f"{letter}:\\\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]:
        if Path(root).exists():
            try:
                total, used, free = shutil.disk_usage(root)
                volumes.append({
                    "root": root,
                    "total_gb": round(total / (1024**3), 2),
                    "used_gb": round(used / (1024**3), 2),
                    "free_gb": round(free / (1024**3), 2),
                    "free_pct": round((free / total) * 100, 1) if total else 0,
                })
            except OSError:
                pass
    return ProbeResult("Storage", Status.OK, [Metric("volumes", volumes, source="shutil.disk_usage")], source="filesystem")


def motherboard_probe() -> ProbeResult:
    blocked = _windows_only("Motherboard")
    if blocked:
        return blocked
    try:
        data = run_json("Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product,Version")
        return ProbeResult("Motherboard", Status.OK, [
            Metric("manufacturer", data.get("Manufacturer"), source="Win32_BaseBoard"),
            Metric("product", data.get("Product"), source="Win32_BaseBoard"),
            Metric("version", data.get("Version"), source="Win32_BaseBoard"),
        ], source="Win32_BaseBoard")
    except Exception as exc:
        return ProbeResult("Motherboard", Status.FAILED, message=str(exc), source="Win32_BaseBoard")


def bios_probe() -> ProbeResult:
    blocked = _windows_only("BIOS")
    if blocked:
        return blocked
    try:
        data = run_json("Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate")
        return ProbeResult("BIOS", Status.OK, [
            Metric("manufacturer", data.get("Manufacturer"), source="Win32_BIOS"),
            Metric("version", data.get("SMBIOSBIOSVersion"), source="Win32_BIOS"),
            Metric("release_date", data.get("ReleaseDate"), source="Win32_BIOS"),
        ], source="Win32_BIOS")
    except Exception as exc:
        return ProbeResult("BIOS", Status.FAILED, message=str(exc), source="Win32_BIOS")


def power_probe() -> ProbeResult:
    blocked = _windows_only("Power")
    if blocked:
        return blocked
    try:
        proc = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "Falha ao consultar powercfg")
        guid_match = re.search(r"([0-9a-fA-F-]{36})", proc.stdout)
        return ProbeResult("Power", Status.OK, [
            Metric("active_scheme", proc.stdout.strip(), source="powercfg /getactivescheme"),
            Metric("active_guid", guid_match.group(1).lower() if guid_match else None, source="powercfg /getactivescheme"),
        ], source="powercfg")
    except Exception as exc:
        return ProbeResult("Power", Status.FAILED, message=str(exc), source="powercfg")


def secure_boot_probe() -> ProbeResult:
    blocked = _windows_only("Secure Boot")
    if blocked:
        return blocked
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        is_admin = False
    if not is_admin:
        return ProbeResult("Secure Boot", Status.REQUIRES_ELEVATION, message="A leitura exige administrador.", source="Confirm-SecureBootUEFI")
    code, out, err = run("Confirm-SecureBootUEFI")
    if code == 0:
        return ProbeResult("Secure Boot", Status.OK, [Metric("enabled", out.lower() == "true", source="Confirm-SecureBootUEFI")], source="Confirm-SecureBootUEFI")
    return ProbeResult("Secure Boot", Status.FAILED, message=err or out, source="Confirm-SecureBootUEFI")


def security_probe() -> ProbeResult:
    blocked = _windows_only("Security")
    if blocked:
        return blocked
    try:
        data = run_json("Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,BehaviorMonitorEnabled")
        return ProbeResult("Security", Status.OK, [
            Metric("antivirus_enabled", data.get("AntivirusEnabled"), source="Get-MpComputerStatus"),
            Metric("realtime_enabled", data.get("RealTimeProtectionEnabled"), source="Get-MpComputerStatus"),
            Metric("behavior_monitor_enabled", data.get("BehaviorMonitorEnabled"), source="Get-MpComputerStatus"),
        ], source="Microsoft Defender")
    except Exception as exc:
        return ProbeResult("Security", Status.PARTIAL, message=str(exc), source="Microsoft Defender")


def startup_probe() -> ProbeResult:
    blocked = _windows_only("Startup")
    if blocked:
        return blocked
    try:
        items = run_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location,User")
        if isinstance(items, dict):
            items = [items]
        return ProbeResult("Startup", Status.OK, [Metric("items", items or [], source="Win32_StartupCommand")], source="Win32_StartupCommand")
    except Exception as exc:
        return ProbeResult("Startup", Status.FAILED, message=str(exc), source="Win32_StartupCommand")


def update_probe() -> ProbeResult:
    blocked = _windows_only("Windows Update")
    if blocked:
        return blocked
    script = "$s=New-Object -ComObject Microsoft.Update.Session; $r=$s.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0'); [PSCustomObject]@{Pending=$r.Updates.Count}"
    try:
        data = run_json(script, timeout=12)
        return ProbeResult("Windows Update", Status.OK, [Metric("pending_count", int(data.get("Pending") or 0), source="Windows Update Agent")], source="Windows Update Agent")
    except Exception as exc:
        return ProbeResult("Windows Update", Status.PARTIAL, message=str(exc), source="Windows Update Agent")



def drivers_probe() -> ProbeResult:
    blocked = _windows_only("Drivers")
    if blocked:
        return blocked
    try:
        drivers = run_json(
            "Get-CimInstance Win32_PnPSignedDriver | "
            "Select-Object DeviceName,DeviceClass,Manufacturer,DriverProviderName,DriverVersion,DriverDate,IsSigned"
        )
        if isinstance(drivers, dict):
            drivers = [drivers]
        drivers = drivers or []
        unsigned = sum(1 for item in drivers if item.get("IsSigned") is False)
        return ProbeResult("Drivers", Status.OK, [
            Metric("count", len(drivers), source="Win32_PnPSignedDriver"),
            Metric("unsigned_count", unsigned, source="Win32_PnPSignedDriver"),
            Metric("items", drivers, source="Win32_PnPSignedDriver"),
        ], source="Win32_PnPSignedDriver")
    except Exception as exc:
        return ProbeResult("Drivers", Status.FAILED, message=str(exc), source="Win32_PnPSignedDriver")


def devices_probe() -> ProbeResult:
    blocked = _windows_only("Devices")
    if blocked:
        return blocked
    try:
        devices = run_json(
            "Get-CimInstance Win32_PnPEntity | "
            "Where-Object { $_.ConfigManagerErrorCode -ne 0 } | "
            "Select-Object Name,Manufacturer,PNPClass,DeviceID,ConfigManagerErrorCode"
        )
        if isinstance(devices, dict):
            devices = [devices]
        devices = devices or []
        return ProbeResult("Devices", Status.OK, [
            Metric("problem_count", len(devices), source="Win32_PnPEntity"),
            Metric("problems", devices, source="Win32_PnPEntity"),
        ], source="Win32_PnPEntity")
    except Exception as exc:
        return ProbeResult("Devices", Status.FAILED, message=str(exc), source="Win32_PnPEntity")


def network_probe() -> ProbeResult:
    blocked = _windows_only("Network")
    if blocked:
        return blocked
    try:
        adapters = run_json(
            "Get-CimInstance Win32_NetworkAdapterConfiguration | "
            "Where-Object { $_.IPEnabled -eq $true } | "
            "Select-Object Description,MACAddress,IPAddress,DefaultIPGateway,DNSServerSearchOrder,DHCPEnabled"
        )
        if isinstance(adapters, dict):
            adapters = [adapters]
        adapters = adapters or []
        return ProbeResult("Network", Status.OK, [
            Metric("adapter_count", len(adapters), source="Win32_NetworkAdapterConfiguration"),
            Metric("adapters", adapters, source="Win32_NetworkAdapterConfiguration"),
        ], source="Win32_NetworkAdapterConfiguration")
    except Exception as exc:
        return ProbeResult("Network", Status.FAILED, message=str(exc), source="Win32_NetworkAdapterConfiguration")


def integrity_probe() -> ProbeResult:
    blocked = _windows_only("Integrity")
    if blocked:
        return blocked
    try:
        script = (
            "$pending=$false; "
            "$paths=@("
            "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending',"
            "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired'"
            "); "
            "foreach($p in $paths){ if(Test-Path $p){$pending=$true} }; "
            "[PSCustomObject]@{PendingReboot=$pending}"
        )
        data = run_json(script)
        return ProbeResult("Integrity", Status.NOT_CHECKED, [
            Metric("pending_reboot", bool(data.get("PendingReboot")), source="Windows servicing registry"),
            Metric("sfc_checked", False, source="policy"),
            Metric("dism_checked", False, source="policy"),
        ], message="SFC e DISM não foram executados; a integridade de arquivos do sistema ainda não foi confirmada.", source="servicing state")
    except Exception as exc:
        return ProbeResult("Integrity", Status.PARTIAL, message=str(exc), source="servicing state")

def collect_all() -> list[ProbeResult]:
    probes = [windows_probe, cpu_probe, ram_probe, gpu_probe, storage_probe, motherboard_probe, bios_probe, power_probe, secure_boot_probe, tpm_probe, security_probe, firewall_probe, startup_probe, update_probe, drivers_probe, devices_probe, network_probe, network_link_probe, proxy_probe, services_probe, scheduled_tasks_probe, processes_probe, integrity_probe]
    results: list[ProbeResult] = []
    for probe in probes:
        try:
            results.append(probe())
        except Exception as exc:
            results.append(ProbeResult(probe.__name__, Status.FAILED, message=str(exc)))
    return results
