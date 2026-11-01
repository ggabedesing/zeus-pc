"""Small native Windows reads; no shell, WMI scan or model runtime."""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import time
from ctypes import wintypes

from .actions import list_power_plans
from .observer import NativeWindowsSampler, _MEMORYSTATUSEX
from .cleanup import preview_temp, temp_root
from .startup import list_startup
from .windows_preferences import states as preference_states

SAMPLER = NativeWindowsSampler()


def read_animations() -> bool:
    if os.name != 'nt':
        raise OSError('Disponível apenas no Windows.')
    enabled = wintypes.BOOL()
    if not ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
        raise OSError('O Windows não informou o estado das animações.')
    return bool(enabled.value)


def write_animations(enabled: bool) -> None:
    if os.name != 'nt' or not isinstance(enabled, bool):
        raise ValueError('Estado de animações inválido.')
    # SPI_SETCLIENTAREAANIMATION, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE.
    # pvParam is the BOOL value (not a pointer) for this SET operation.
    if not ctypes.windll.user32.SystemParametersInfoW(0x1043, 0, ctypes.c_void_p(int(enabled)), 3):
        raise OSError('O Windows não permitiu alterar as animações.')


class PowerStatus(ctypes.Structure):
    _fields_ = [('ac', wintypes.BYTE), ('flags', wintypes.BYTE),
                ('percent', wintypes.BYTE), ('reserved', wintypes.BYTE),
                ('life', wintypes.DWORD), ('full_life', wintypes.DWORD)]


def read_ac() -> bool | None:
    if os.name != 'nt':
        return None
    status = PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    return {0: False, 1: True}.get(status.ac)


def quick_snapshot() -> dict:
    snapshot = {
        'supported': os.name == 'nt', 'computer': platform.node(),
        'system': platform.system(), 'cpu_pct': None, 'cpu_name': None,
        'ram_used_pct': None, 'ram_total_gb': None, 'ram_available_gb': None,
        'disk_free_gb': None, 'disk_total_gb': None, 'disk_free_pct': None,
        'ac_connected': read_ac(), 'animations_enabled': None, 'power_plans': [],
        'captured_at': time.time(), 'read_errors': [], 'startup': [],
        'temp_bytes': 0, 'temp_count': 0, '_temp_manifest': [], 'preferences': {},
    }
    if not snapshot['supported']:
        return snapshot
    snapshot['preferences'] = preference_states()
    memory = _MEMORYSTATUSEX()
    memory.dwLength = ctypes.sizeof(memory)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        snapshot.update(ram_used_pct=float(memory.dwMemoryLoad),
                        ram_total_gb=round(memory.ullTotalPhys / 1024**3, 1),
                        ram_available_gb=round(memory.ullAvailPhys / 1024**3, 1))
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            snapshot['cpu_name'] = str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
    except OSError:
        pass
    try:
        disk = shutil.disk_usage(os.environ.get('SystemDrive', 'C:') + os.sep)
        snapshot.update(disk_free_gb=round(disk.free / 1024**3, 1),
                        disk_total_gb=round(disk.total / 1024**3, 1),
                        disk_free_pct=round(disk.free / disk.total * 100, 1))
    except OSError:
        snapshot['read_errors'].append('Espaço em disco indisponível.')
    try:
        snapshot['power_plans'] = list_power_plans()
    except (OSError, TimeoutError) as exc:
        snapshot['read_errors'].append(f'Energia indisponível: {exc}')
    try:
        snapshot['animations_enabled'] = read_animations()
    except OSError:
        snapshot['read_errors'].append('Estado das animações indisponível.')
    SAMPLER.cpu_pct()
    time.sleep(0.15)
    snapshot['cpu_pct'] = SAMPLER.cpu_pct()
    try:
        snapshot['startup'] = list_startup()
    except OSError:
        snapshot['read_errors'].append('Inicialização indisponível.')
    preview = preview_temp(temp_root())
    snapshot.update(temp_bytes=preview['bytes'], temp_count=len(preview['files']),
                    temp_partial=preview['truncated'], _temp_manifest=preview['files'])
    return snapshot
