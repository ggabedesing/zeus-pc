from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any

from .powershell import run_json


@dataclass(slots=True)
class Sample:
    timestamp: float
    cpu_pct: float | None
    ram_used_pct: float | None
    gpu_pct: float | None
    gpu_temp_c: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_sample() -> Sample:
    now = time.time()
    cpu = ram = gpu = temperature = None
    if os.name == "nt":
        try:
            data = run_json("Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average | Select-Object @{n='Cpu';e={$_.Average}}", timeout=3)
            cpu = float(data.get("Cpu")) if data and data.get("Cpu") is not None else None
        except Exception:
            pass
        try:
            data = run_json("Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory", timeout=3)
            total = float(data.get("TotalVisibleMemorySize") or 0)
            free = float(data.get("FreePhysicalMemory") or 0)
            if total:
                ram = round((1 - free / total) * 100, 1)
        except Exception:
            pass
        try:
            smi = shutil.which("nvidia-smi")
            if smi:
                proc = subprocess.run(
                    [smi, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    parts = [part.strip() for part in proc.stdout.strip().splitlines()[0].split(",")]
                    if len(parts) >= 2:
                        gpu, temperature = float(parts[0]), float(parts[1])
        except Exception:
            pass
    return Sample(now, cpu, ram, gpu, temperature)
