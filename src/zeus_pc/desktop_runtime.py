"""Zeus-owned animated desktop: stdlib process control and native Win32/GDI.

Importing this module never creates a window. WorkerW is an undocumented
Explorer hosting convention, so failure is explicit; there is no overlay fallback.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .app_paths import data_dir

_CONTROL_LOCK = threading.RLock()
_LIVE = {'starting', 'running', 'paused', 'stopping'}
_SCENES = (
    {'id': 'aurora', 'name': 'Aurora', 'description': 'Faixas de luz verde e violeta em movimento suave.',
     'colors': ['#06141f', '#35d7ac', '#8469d5']},
    {'id': 'particles', 'name': 'Constelação', 'description': 'Partículas luminosas em um céu azul profundo.',
     'colors': ['#07111e', '#6dc9f2', '#d4e9ff']},
    {'id': 'waves', 'name': 'Ondas', 'description': 'Camadas de ondas azuis com movimento contínuo.',
     'colors': ['#071626', '#176d98', '#52b8bd']},
)


def catalog():
    return {'scenes': [{**s, 'colors': list(s['colors'])} for s in _SCENES]}


def _windows_supported():
    return os.name == 'nt'


def _read(path):
    try:
        if path.stat().st_size > 65536:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _process_identity(pid):
    """Creation FILETIME of a *live* process; no termination or process mutation."""
    if not _windows_supported() or not isinstance(pid, int) or pid <= 0:
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return None
        return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _worker_command(scene, folder, token):
    prefix = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'zeus_pc.desktop_runtime']
    return [*prefix, '--desktop-worker', scene, '--runtime-dir', str(Path(folder).resolve()), '--token', token]


def _describe(record):
    return dict(record, running=record.get('state') in {'running', 'paused', 'stopping'},
                active=record.get('state') in _LIVE, supported=_windows_supported(), fps=24, fps_limit=24)


class Runtime:
    """Controller. data_directory is the application data root, not the worker dir."""
    def __init__(self, data_directory=None):
        self.folder = (Path(data_directory) if data_directory is not None else data_dir()).resolve() / 'desktop-runtime'

    def status(self):
        with _CONTROL_LOCK:
            record = _read(self.folder / 'status.json')
            if not record:
                return _describe({'state': 'stopped', 'scene': None, 'pid': None, 'message': ''})
            result = dict(record)
            if record.get('state') in _LIVE:
                pid, expected = record.get('pid'), record.get('process_started')
                if pid is None and time.time() - record.get('updated_at', 0) < 5:
                    return _describe(result)
                if not expected or _process_identity(pid) != expected:
                    result.update(state='stopped', pid=None, message='O processo da área de trabalho foi encerrado.')
                elif time.time() - record.get('updated_at', 0) > 12:
                    result.update(state='failed', message='O renderizador não está respondendo. Solicite a parada antes de iniciar novamente.')
                elif _read(self.folder / 'stop.json').get('token') == record.get('token'):
                    result.update(state='stopping')
            return _describe(result)

    def start(self, scene):
        if scene not in {s['id'] for s in _SCENES}:
            raise ValueError('Cena da área de trabalho inválida.')
        if not _windows_supported():
            raise ValueError('A área de trabalho animada requer Windows e Explorer.')
        with _CONTROL_LOCK:
            current = self.status()
            saved = _read(self.folder / 'status.json')
            identity = saved.get('process_started')
            alive = identity and _process_identity(saved.get('pid')) == identity
            starting = current['state'] == 'starting' and time.time() - saved.get('updated_at', 0) < 5
            if alive or starting:
                if current['state'] in {'running', 'paused', 'starting'} and current.get('scene') == scene:
                    return current
                raise ValueError('Pare a cena atual antes de iniciar outra.')
            token = secrets.token_hex(16)
            record = dict(state='starting', scene=scene, pid=None, process_started=None,
                          token=token, updated_at=time.time(), message='Preparando a camada da área de trabalho.')
            _write(self.folder / 'status.json', record)
            try:
                environment = dict(os.environ)
                # -m must work when the app was launched from outside the source tree.
                if not getattr(sys, 'frozen', False):
                    source = str(Path(__file__).resolve().parents[1])
                    environment['PYTHONPATH'] = source + (os.pathsep + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
                child = subprocess.Popen(_worker_command(scene, self.folder, token), env=environment,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
                # A fast worker may already have published a richer state; preserve it.
                latest = _read(self.folder / 'status.json')
                if latest.get('token') == token and latest.get('state') == 'starting':
                    identity = _process_identity(child.pid)
                    latest.update(pid=child.pid, process_started=identity)
                    _write(self.folder / 'status.json', latest)
                return self.status()
            except OSError as exc:
                record.update(state='failed', message=str(exc), updated_at=time.time())
                _write(self.folder / 'status.json', record)
                return self.status()

    def stop(self):
        """Cooperative request only: never terminate a PID or someone else's window."""
        with _CONTROL_LOCK:
            record = _read(self.folder / 'status.json')
            current = self.status()
            identity = record.get('process_started')
            starting = record.get('state') == 'starting' and time.time() - record.get('updated_at', 0) < 5
            if not starting and (not identity or _process_identity(record.get('pid')) != identity):
                return current
            _write(self.folder / 'stop.json', {'token': record['token'], 'requested_at': time.time()})
            return _describe(dict(current, state='stopping', message='Encerrando a cena e liberando os recursos gráficos.'))


def _worker_run(scene, folder, token):
    folder = Path(folder)
    owner = _read(folder / 'status.json')
    if owner.get('token') != token or scene not in {s['id'] for s in _SCENES}:
        return 2
    renderer = None
    pid = os.getpid()
    identity = _process_identity(pid)

    def should_stop():
        return (_read(folder / 'status.json').get('token') != token
                or _read(folder / 'stop.json').get('token') == token)

    def publish(state, message=''):
        if _read(folder / 'status.json').get('token') != token:
            return
        _write(folder / 'status.json', dict(state=state, scene=scene, pid=pid,
               process_started=identity, token=token, updated_at=time.time(), message=message, fps_limit=24))

    try:
        if should_stop():
            publish('stopped')
            return 0
        renderer = _NativeRenderer(scene)
        renderer.run(should_stop, publish)
        renderer.close()
        renderer = None
        publish('stopped')
        return 0
    except Exception as exc:
        publish('failed', str(exc))
        return 1
    finally:
        if renderer is not None:
            renderer.close()


class _NativeRenderer:
    """Own child HWND, own GDI buffers, capped loop; no Explorer code injection."""
    def __init__(self, scene):
        if not _windows_supported():
            raise OSError('Renderizador disponível apenas no Windows.')
        self.scene = scene
        self.hwnd = self.dc = self.buffer = self.bitmap = self.old_bitmap = 0
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._configure()
        try:
            self._create()
        except Exception:
            self.close()
            raise

    def _configure(self):
        u, g, k = self.user, self.gdi, self.kernel
        ptr, integer, uint = ctypes.c_void_p, ctypes.c_int, wintypes.UINT
        specs = {
            'FindWindowW': ([wintypes.LPCWSTR, wintypes.LPCWSTR], ptr),
            'FindWindowExW': ([ptr, ptr, wintypes.LPCWSTR, wintypes.LPCWSTR], ptr),
            'SendMessageTimeoutW': ([ptr, uint, ctypes.c_size_t, ctypes.c_ssize_t, uint, uint, ctypes.POINTER(ctypes.c_size_t)], ctypes.c_ssize_t),
            'CreateWindowExW': ([wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, integer, integer, integer, integer, ptr, ptr, ptr, ptr], ptr),
            'DefWindowProcW': ([ptr, uint, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
            'DestroyWindow': ([ptr], wintypes.BOOL), 'GetDC': ([ptr], ptr),
            'ReleaseDC': ([ptr, ptr], integer), 'IsWindow': ([ptr], wintypes.BOOL),
            'IsWindowVisible': ([ptr], wintypes.BOOL),
            'GetForegroundWindow': ([], ptr), 'GetAncestor': ([ptr, uint], ptr),
            'GetClassNameW': ([ptr, wintypes.LPWSTR, integer], integer),
            'GetWindowRect': ([ptr, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            'ScreenToClient': ([ptr, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            'SetWindowPos': ([ptr, ptr, integer, integer, integer, integer, uint], wintypes.BOOL),
            'ShowWindow': ([ptr, integer], wintypes.BOOL),
            'ValidateRect': ([ptr, ptr], wintypes.BOOL),
            'PeekMessageW': ([ctypes.POINTER(wintypes.MSG), ptr, uint, uint, uint], wintypes.BOOL),
            'TranslateMessage': ([ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
            'DispatchMessageW': ([ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
            'FillRect': ([ptr, ctypes.POINTER(wintypes.RECT), ptr], integer),
        }
        for name, (args, result) in specs.items():
            fn = getattr(u, name); fn.argtypes = args; fn.restype = result
        for name, args, result in [
            ('CreateCompatibleDC', [ptr], ptr), ('CreateCompatibleBitmap', [ptr, integer, integer], ptr),
            ('SelectObject', [ptr, ptr], ptr), ('DeleteObject', [ptr], wintypes.BOOL),
            ('DeleteDC', [ptr], wintypes.BOOL), ('CreateSolidBrush', [wintypes.DWORD], ptr),
            ('BitBlt', [ptr, integer, integer, integer, integer, ptr, integer, integer, wintypes.DWORD], wintypes.BOOL),
            ('Ellipse', [ptr, integer, integer, integer, integer], wintypes.BOOL),
            ('Polygon', [ptr, ctypes.POINTER(wintypes.POINT), integer], wintypes.BOOL),
            ('GetStockObject', [integer], ptr),
        ]:
            fn = getattr(g, name); fn.argtypes = args; fn.restype = result
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]; k.GetModuleHandleW.restype = ptr
        self._enum_type = ctypes.WINFUNCTYPE(wintypes.BOOL, ptr, ctypes.c_ssize_t)
        u.EnumWindows.argtypes = [self._enum_type, ctypes.c_ssize_t]
        self._proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ptr, uint, ctypes.c_size_t, ctypes.c_ssize_t)
        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style', uint), ('lpfnWndProc', self._proc_type), ('cbClsExtra', integer),
                        ('cbWndExtra', integer), ('hInstance', ptr), ('hIcon', ptr), ('hCursor', ptr),
                        ('hbrBackground', ptr), ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]
        self._class_type = WNDCLASS
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]; u.RegisterClassW.restype = wintypes.ATOM
        u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, ptr]

    def _desktop_host(self):
        u = self.user
        progman = u.FindWindowW('Progman', None)
        if not progman:
            raise OSError('O Explorer não disponibilizou a área de trabalho Progman.')
        result = ctypes.c_size_t()
        # Explorer convention, NOT a supported public Microsoft wallpaper API.
        # Only ask the shell for its desktop layer; never hide its icon windows.
        u.SendMessageTimeoutW(progman, 0x052C, 0xD, 0, 2, 1000, ctypes.byref(result))
        u.SendMessageTimeoutW(progman, 0x052C, 0xD, 1, 2, 1000, ctypes.byref(result))
        found = []
        @self._enum_type
        def visit(hwnd, _):
            if u.FindWindowExW(hwnd, None, 'SHELLDLL_DefView', None):
                behind = u.FindWindowExW(None, hwnd, 'WorkerW', None)
                if behind and u.IsWindowVisible(behind):
                    found.append(behind)
            return True
        u.EnumWindows(visit, 0)
        if not found:
            raise OSError('A camada WorkerW atrás dos ícones não está disponível nesta versão do Explorer. Nenhuma sobreposição foi criada.')
        return found[0]

    def _bounds(self):
        return tuple(self.user.GetSystemMetrics(index) for index in (76, 77, 78, 79))

    def _create(self):
        u = self.user
        try:
            u.SetProcessDPIAware()
        except AttributeError:
            pass
        self.parent = self._desktop_host()
        self.bounds = self._bounds()
        x, y, width, height = self.bounds
        if width <= 0 or height <= 0 or width > 32768 or height > 32768:
            raise OSError('Dimensões da área de trabalho indisponíveis.')
        self.width, self.height = width, height
        position = wintypes.POINT(x, y)
        if not u.ScreenToClient(self.parent, ctypes.byref(position)):
            raise OSError('Não foi possível localizar a camada da área de trabalho.')
        self._instance = self.kernel.GetModuleHandleW(None)
        self._classname = 'ZeusDesktopScene_' + str(os.getpid())
        @self._proc_type
        def window_proc(hwnd, message, wparam, lparam):
            if message == 0x14:  # WM_ERASEBKGND: own double-buffered paint.
                return 1
            if message == 0x0F:
                u.ValidateRect(hwnd, None)
                return 0
            if message == 0x84:  # HTTRANSPARENT: never take desktop clicks.
                return -1
            if message == 0x10:
                u.DestroyWindow(hwnd)
                return 0
            return u.DefWindowProcW(hwnd, message, wparam, lparam)
        self._window_proc = window_proc
        wc = self._class_type(lpfnWndProc=window_proc, hInstance=self._instance, lpszClassName=self._classname)
        if not u.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._registered = True
        # WS_CHILD, no taskbar entry, no activation; create directly under WorkerW.
        self.hwnd = u.CreateWindowExW(0x080000A0, self._classname, 'Zeus Desktop', 0x40000000,
                    position.x, position.y, width, height, self.parent, None, self._instance, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.dc = u.GetDC(self.hwnd)
        self.buffer = self.gdi.CreateCompatibleDC(self.dc)
        self.bitmap = self.gdi.CreateCompatibleBitmap(self.dc, width, height)
        if not self.dc or not self.buffer or not self.bitmap:
            raise OSError('Não foi possível reservar os recursos gráficos da cena.')
        self.old_bitmap = self.gdi.SelectObject(self.buffer, self.bitmap)
        self.gdi.SelectObject(self.buffer, self.gdi.GetStockObject(8))  # NULL_PEN
        self._draw(0)
        u.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

    @staticmethod
    def _color(red, green, blue):
        return int(red) | (int(green) << 8) | (int(blue) << 16)

    def _fill(self, color, rect=None, points=None, ellipse=None):
        brush = self.gdi.CreateSolidBrush(self._color(*color))
        if not brush:
            raise OSError('Falha ao desenhar a cena.')
        previous = self.gdi.SelectObject(self.buffer, brush)
        try:
            if rect is not None:
                self.user.FillRect(self.buffer, ctypes.byref(wintypes.RECT(*rect)), brush)
            elif points is not None:
                values = (wintypes.POINT * len(points))(*(wintypes.POINT(*p) for p in points))
                self.gdi.Polygon(self.buffer, values, len(points))
            elif ellipse is not None:
                self.gdi.Ellipse(self.buffer, *ellipse)
        finally:
            self.gdi.SelectObject(self.buffer, previous)
            self.gdi.DeleteObject(brush)

    def _draw(self, elapsed):
        w, h = self.width, self.height
        # A small fixed number of primitives; cost does not scale with icon count.
        for i in range(48):
            fraction = i / 47
            self._fill((5 + fraction * 5, 12 + fraction * 14, 24 + fraction * 18),
                       rect=(0, i * h // 48, w, (i + 1) * h // 48 + 1))
        if self.scene == 'particles':
            for i in range(75):
                x = int(((i * .61803398875 + elapsed * .003 * (1 + i % 3)) % 1) * w)
                y = int(((i * .41421356237 - elapsed * .005 * (1 + i % 2)) % 1) * h)
                glow = .55 + .45 * math.sin(elapsed * .6 + i)
                radius = 1 + i % 3
                self._fill((70 + 90 * glow, 125 + 90 * glow, 165 + 80 * glow),
                           ellipse=(x - radius, y - radius, x + radius + 1, y + radius + 1))
        else:
            layers = 25 if self.scene == 'aurora' else 12
            for layer in range(layers):
                f = layer / layers
                points = []
                for j in range(49):
                    x = j * w // 48
                    wave = math.sin(j / 48 * math.tau * 1.2 + elapsed * .12 + f * 2.3)
                    wave += .35 * math.sin(j / 48 * math.tau * 2.1 - elapsed * .08)
                    y = int(h * (.38 + f * .42) + wave * h * (.08 + f * .04))
                    points.append((x, y))
                if self.scene == 'aurora':
                    lower = [(x, y + max(4, h // 80)) for x, y in reversed(points)]
                    color = (20 + 65 * f, 65 + 95 * (1 - f), 100 + 55 * f)
                    self._fill(color, points=[*points, *lower])
                else:
                    self._fill((12 + 28 * f, 43 + 65 * f, 68 + 67 * f), points=[*points, (w, h), (0, h)])
        if not self.gdi.BitBlt(self.dc, 0, 0, w, h, self.buffer, 0, 0, 0x00CC0020):
            raise OSError('A camada da área de trabalho deixou de aceitar a renderização.')

    def _pause_reason(self):
        class POWER(ctypes.Structure):
            _fields_ = [('ac', wintypes.BYTE), ('flags', wintypes.BYTE), ('percent', wintypes.BYTE),
                        ('reserved', wintypes.BYTE), ('life', wintypes.DWORD), ('full_life', wintypes.DWORD)]
        power = POWER()
        if self.kernel.GetSystemPowerStatus(ctypes.byref(power)) and power.ac == 0:
            return 'Pausado para economizar bateria.'
        foreground = self.user.GetForegroundWindow()
        if foreground and foreground not in {self.hwnd, self.parent}:
            name = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(foreground, name, 256)
            if name.value not in {'Progman', 'WorkerW', 'Shell_TrayWnd'}:
                monitor = self.user.MonitorFromWindow(foreground, 2)
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                                ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
                info = MONITORINFO(); info.cbSize = ctypes.sizeof(info)
                rect = wintypes.RECT()
                if self.user.GetMonitorInfoW(monitor, ctypes.byref(info)) and self.user.GetWindowRect(foreground, ctypes.byref(rect)):
                    m = info.rcMonitor
                    if rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom:
                        return 'Pausado enquanto um aplicativo ocupa a tela inteira.'
        return ''

    def run(self, should_stop, publish):
        self.user.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self.user.MonitorFromWindow.restype = ctypes.c_void_p
        self.user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.kernel.GetSystemPowerStatus.argtypes = [ctypes.c_void_p]
        started = time.monotonic(); last_heartbeat = 0; last_probe = 0
        paused = ''; last_state = None
        message = wintypes.MSG()
        while not should_stop():
            frame = time.monotonic()
            if not self.user.IsWindow(self.parent) or not self.user.IsWindow(self.hwnd):
                raise OSError('O Explorer recriou a área de trabalho. Inicie a cena novamente.')
            while self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                if message.message == 0x12:
                    return
                self.user.TranslateMessage(ctypes.byref(message)); self.user.DispatchMessageW(ctypes.byref(message))
            if frame - last_probe >= 1:
                if self._bounds() != self.bounds:
                    raise OSError('A configuração dos monitores mudou. Inicie a cena novamente para ajustar as dimensões.')
                paused = self._pause_reason(); last_probe = frame
            state = 'paused' if paused else 'running'
            if frame - last_heartbeat >= 1 or state != last_state:
                publish(state, paused); last_heartbeat = frame; last_state = state
            if not paused:
                self._draw(frame - started)
            time.sleep(max(0.002, (0.2 if paused else 1 / 24) - (time.monotonic() - frame)))

    def close(self):
        if self.buffer and self.old_bitmap:
            self.gdi.SelectObject(self.buffer, self.old_bitmap)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap); self.bitmap = 0
        if self.buffer:
            self.gdi.DeleteDC(self.buffer); self.buffer = 0
        if self.dc and self.hwnd:
            self.user.ReleaseDC(self.hwnd, self.dc); self.dc = 0
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd); self.hwnd = 0
        if getattr(self, '_registered', False):
            self.user.UnregisterClassW(self._classname, self._instance); self._registered = False


def worker_main(argv=None):
    parser = argparse.ArgumentParser(description='Zeus native desktop worker')
    parser.add_argument('--desktop-worker', required=True, choices=[s['id'] for s in _SCENES])
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--token', required=True)
    args = parser.parse_args(argv)
    if len(args.token) != 32 or any(c not in '0123456789abcdef' for c in args.token):
        parser.error('invalid worker token')
    return _worker_run(args.desktop_worker, Path(args.runtime_dir).resolve(), args.token)


if __name__ == '__main__':
    raise SystemExit(worker_main())
