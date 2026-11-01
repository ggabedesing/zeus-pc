"""Explicitly started Zeus dock/sidebar, using only Python/Tk and public Win32.

Importing, constructing Runtime and reading catalog never creates a window.
The optional taskbar visibility change has an on-disk recovery journal.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser

from .app_paths import data_dir

_TOKEN = re.compile(r'^[0-9a-f]{64}$')
_BAR_CLASSES = ('Shell_TrayWnd', 'Shell_SecondaryTrayWnd')
_THREAD_LOCK = threading.RLock()
_STOP_TIMEOUT = 2.5
_spawn = subprocess.Popen


def catalog():
    return {'layouts': [
        {'id': 'dock', 'name': 'Dock flutuante',
         'description': 'Atalhos do Zeus centralizados na parte inferior, com relógio.'},
        {'id': 'sidebar', 'name': 'Barra lateral',
         'description': 'Um painel compacto à esquerda com Explorer, Ajustes e Zeus.'},
    ]}


class NativeWindows:
    supported = os.name == 'nt'

    @staticmethod
    def _libraries():
        if os.name != 'nt':
            raise OSError('Layouts disponíveis apenas no Windows.')
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        return user, kernel

    def _process(self, pid, access=0x101000):
        _, kernel = self._libraries()
        handle = kernel.OpenProcess(access, False, pid)
        return kernel, handle

    @staticmethod
    def _stamp(kernel, handle):
        creation, exit_, system, user = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                      ctypes.byref(system), ctypes.byref(user)):
            return None
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)

    def process_stamp(self, pid):
        kernel, handle = self._process(pid)
        if not handle:
            return None
        try:
            return self._stamp(kernel, handle)
        finally:
            kernel.CloseHandle(handle)

    def process_alive(self, pid, stamp=None):
        if not isinstance(pid, int) or pid <= 0:
            return False
        kernel, handle = self._process(pid)
        if not handle:
            return False
        try:
            return (kernel.WaitForSingleObject(handle, 0) == 0x102
                    and (stamp is None or self._stamp(kernel, handle) == stamp))
        finally:
            kernel.CloseHandle(handle)

    def terminate_worker(self, pid, stamp):
        # Never terminate an unverified/reused PID, and never terminate this app.
        if not stamp or pid == os.getpid():
            return
        kernel, handle = self._process(pid, 0x101001)
        if not handle:
            return
        try:
            if self._stamp(kernel, handle) == stamp:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 1000)
        finally:
            kernel.CloseHandle(handle)

    def identity(self, hwnd):
        user, _ = self._libraries()
        name = ctypes.create_unicode_buffer(256)
        pid = wintypes.DWORD()
        if not user.GetClassNameW(hwnd, name, len(name)):
            return None
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        return dict(hwnd=int(hwnd), pid=pid.value, class_name=name.value,
                    visible=bool(user.IsWindowVisible(hwnd)), process_stamp=self.process_stamp(pid.value))

    def list_bars(self):
        user, _ = self._libraries()
        bars = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            item = self.identity(hwnd)
            if item and item['class_name'] in _BAR_CLASSES:
                bars.append(item)
            return True
        callback = callback_type(visit)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        if not user.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return bars

    def set_visible(self, hwnd, visible):
        user, _ = self._libraries()
        user.ShowWindow(hwnd, 8 if visible else 0)  # SW_SHOWNA / SW_HIDE.
        if bool(user.IsWindowVisible(hwnd)) is not visible:
            raise OSError('O Windows não confirmou a visibilidade da barra.')

    def work_area(self):
        user, _ = self._libraries()
        rect = wintypes.RECT()
        user.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        if not user.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top, rect.right, rect.bottom

    def round_own_window(self, hwnd):
        # An optional preference on our own HWND. Older Windows simply ignores it.
        try:
            user, _ = self._libraries()
            user.GetParent.argtypes = [wintypes.HWND]
            user.GetParent.restype = wintypes.HWND
            target = user.GetParent(hwnd) or hwnd
            dwm = ctypes.WinDLL('dwmapi')
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            preference = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(target, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        except OSError:
            pass


_native = NativeWindows()


@contextmanager
def _locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        path = directory / 'runtime.lock'
        with path.open('a+b') as file:
            file.seek(0, 2)
            if file.tell() == 0:
                file.write(b'\0')
                file.flush()
            file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                file.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file, fcntl.LOCK_UN)


def _read(directory):
    path = directory / 'state.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 256 * 1024:
        raise ValueError('Estado do layout inválido.')
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or not _TOKEN.fullmatch(str(state.get('token', ''))):
        raise ValueError('Estado do layout inválido.')
    return state


def _write(directory, state):
    path = directory / ('state-' + state['token'] + '.tmp')
    if path.is_symlink():
        raise ValueError('Arquivo do runtime inválido.')
    with path.open('w', encoding='utf-8') as file:
        json.dump(state, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(path, directory / 'state.json')


def _matches(original, current):
    return bool(current and all(original.get(key) == current.get(key)
                for key in ('hwnd', 'pid', 'class_name', 'process_stamp')))


def _hide_bars(directory, state):
    for original in _native.list_bars():
        if not original['visible'] or original['class_name'] not in _BAR_CLASSES:
            continue
        # Journal before touching the shell; a crash before SW_HIDE needs no
        # recovery because the bar remains visible. A crash after it is covered.
        entry = dict(original, was_visible=True, hidden=True)
        state['bars'].append(entry)
        _write(directory, state)
        if not _matches(original, _native.identity(original['hwnd'])):
            entry['hidden'] = False
            _write(directory, state)
            continue
        _native.set_visible(original['hwnd'], False)
        _write(directory, state)


def _restore_bars(directory, state):
    errors = []
    for entry in state.get('bars', []):
        if not entry.get('hidden') or entry.get('was_visible') is not True:
            continue
        if entry.get('class_name') not in _BAR_CLASSES:
            continue
        try:
            current = _native.identity(entry['hwnd'])
            if _matches(entry, current) and not current['visible']:
                _native.set_visible(entry['hwnd'], True)
            # Explorer restarts/reused HWNDs do not belong to this session.
            entry['hidden'] = False
        except OSError as exc:
            errors.append(str(exc))
    state['restore_errors'] = errors
    _write(directory, state)
    return not errors


def _is_running(state):
    return bool(state and state.get('state') in ('STARTING', 'RUNNING', 'STOPPING')
                and _native.process_alive(state.get('pid', 0), state.get('process_stamp')))


def _public(state):
    return dict(supported=_native.supported, running=_is_running(state),
                state=state.get('state', 'STOPPED') if state else 'STOPPED',
                layout=state.get('layout') if state else None,
                hide_native_taskbar=bool(state and state.get('hide_native_taskbar')),
                native_taskbar_hidden=bool(state and any(x.get('hidden') for x in state.get('bars', []))),
                pid=state.get('pid') if state else None,
                error=state.get('error') if state else None,
                restore_errors=state.get('restore_errors', []) if state else [])


class Runtime:
    def __init__(self, runtime_dir=None):
        self.directory = Path(runtime_dir) if runtime_dir is not None else data_dir() / 'layout-runtime'

    def _status_locked(self):
        state = _read(self.directory)
        if state and not _is_running(state):
            if state.get('state') in ('STARTING', 'RUNNING') and not state.get('error'):
                state['error'] = 'O processo do layout foi interrompido; recuperação da barra nativa solicitada.'
            _restore_bars(self.directory, state)
            state['state'] = 'ERROR' if state.get('error') or state.get('restore_errors') else 'STOPPED'
            _write(self.directory, state)
        return state

    def status(self):
        if not _native.supported:
            return _public(None)
        with _locked(self.directory):
            return _public(self._status_locked())

    def start(self, layout, hide_native_taskbar=False):
        if layout not in ('dock', 'sidebar') or not isinstance(layout, str):
            raise ValueError('Escolha dock ou sidebar.')
        if type(hide_native_taskbar) is not bool:
            raise ValueError('hide_native_taskbar deve ser true ou false.')
        if not _native.supported:
            raise OSError('Layouts disponíveis apenas no Windows.')
        current = self.status()
        if current['running'] and (current['layout'] != layout
                                  or current['hide_native_taskbar'] != hide_native_taskbar):
            if self.stop()['running']:
                raise OSError('O layout anterior ainda está encerrando.')
        with _locked(self.directory):
            state = self._status_locked()
            if _is_running(state):
                return _public(state)
            if state and state.get('restore_errors'):
                raise OSError('Restaure a barra nativa antes de iniciar outro layout.')
            token = secrets.token_hex(32)
            state = dict(token=token, layout=layout, hide_native_taskbar=hide_native_taskbar,
                         parent_pid=os.getpid(), parent_stamp=_native.process_stamp(os.getpid()),
                         pid=0, process_stamp=None, state='STARTING', bars=[], error=None)
            _write(self.directory, state)
            command = [sys.executable]
            if not getattr(sys, 'frozen', False):
                command += ['-m', 'zeus_pc.layout_runtime']
            command += ['--layout-worker', layout, '--runtime-dir', str(self.directory.resolve()),
                        '--token', token, '--parent-pid', str(state['parent_pid'])]
            if hide_native_taskbar:
                command.append('--hide-native-taskbar')
            try:
                process = _spawn(command, shell=False, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                state['pid'] = process.pid
                state['process_stamp'] = _native.process_stamp(process.pid)
            except Exception as exc:
                state.update(state='ERROR', error=str(exc))
                _write(self.directory, state)
                raise
            _write(self.directory, state)
            return _public(state)

    def stop(self):
        if not _native.supported:
            return _public(None)
        with _locked(self.directory):
            state = self._status_locked()
            if not _is_running(state):
                return _public(state)
            token = state['token']
            (self.directory / f'{token}.stop').write_text(token, encoding='ascii')
            state['state'] = 'STOPPING'
            _write(self.directory, state)
            pid, stamp = state['pid'], state.get('process_stamp')
        deadline = time.monotonic() + _STOP_TIMEOUT
        while _native.process_alive(pid, stamp) and time.monotonic() < deadline:
            time.sleep(.05)
        if _native.process_alive(pid, stamp):
            _native.terminate_worker(pid, stamp)
        return self.status()


def _fixed_action(action):
    # This intentionally has no arbitrary command/path argument surface.
    if action == 'explorer':
        os.startfile('shell:MyComputerFolder')
    elif action == 'settings':
        os.startfile('ms-settings:')
    elif action == 'clock':
        os.startfile('ms-settings:dateandtime')
    elif action == 'zeus':
        try:
            port = int(os.environ.get('ZEUS_PORT', '8000'))
        except ValueError:
            port = 8000
        if not 1 <= port <= 65535:
            port = 8000
        webbrowser.open(f'http://127.0.0.1:{port}/')


def _create_window(layout, on_close):
    # Tk is imported only inside the explicitly launched worker.
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.title('Zeus · ' + ('Dock' if layout == 'dock' else 'Barra lateral'))
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.attributes('-alpha', .98)
    transparent, surface, border = '#010203', '#18232e', '#3a4b58'
    root.configure(bg=transparent)
    root.attributes('-transparentcolor', transparent)
    width, height = (424, 92) if layout == 'dock' else (108, 382)
    left, top, right, bottom = _native.work_area()
    x = max(0, (left + right - width) // 2) if layout == 'dock' else left + 16
    y = bottom - height - 16 if layout == 'dock' else max(top + 16, (top + bottom - height) // 2)
    root.geometry(f'{width}x{height}+{x}+{max(0, y)}')
    canvas = tk.Canvas(root, width=width, height=height, bg=transparent, highlightthickness=0)
    canvas.pack()
    def rounded(x1, y1, x2, y2, radius, **options):
        points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,
                  x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,
                  x1,y1+radius,x1,y1]
        return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)
    rounded(1, 1, width-1, height-1, 23, fill=surface, outline=border, width=1)
    font = ('Segoe UI', 8)
    def button(action, label, x, y):
        tag = 'button_' + action
        background = rounded(x-27, y-27, x+27, y+32, 12, fill=surface, outline='', tags=tag)
        if action == 'explorer':
            canvas.create_polygon(x-14,y-9,x-4,y-9,x,y-5,x+14,y-5,x+14,y+10,
                                  x-14,y+10, fill='#d9b96a', outline='#efdba6', tags=tag)
            canvas.create_line(x-11,y-2,x+11,y-2, fill='#f6e6bb', width=2, tags=tag)
        elif action == 'settings':
            for angle in range(0, 360, 45):
                sine, cosine = math.sin(math.radians(angle)), math.cos(math.radians(angle))
                canvas.create_line(x+cosine*10,y+sine*10,x+cosine*16,y+sine*16,
                                   fill='#bdcbd8', width=4, tags=tag)
            canvas.create_oval(x-11,y-11,x+11,y+11, fill='#a8bacb', outline='', tags=tag)
            canvas.create_oval(x-4,y-4,x+4,y+4, fill=surface, outline='', tags=tag)
        else:
            canvas.create_polygon(x-12,y-13,x+13,y-13,x-4,y+4,x+12,y+4,x+12,y+12,
                                  x-13,y+12,x+4,y-5,x-12,y-5, fill='#75e6c1', tags=tag)
        canvas.create_text(x, y+23, text=label, fill='#c3d0dc', font=font, tags=tag)
        canvas.tag_bind(tag, '<Enter>', lambda _: (canvas.itemconfigure(background, fill='#2b3a47'), canvas.configure(cursor='hand2')))
        canvas.tag_bind(tag, '<Leave>', lambda _: (canvas.itemconfigure(background, fill=surface), canvas.configure(cursor='')))
        def activate(_):
            try:
                _fixed_action(action)
            except OSError:
                canvas.itemconfigure(clock, text='Indisponível')
        canvas.tag_bind(tag, '<Button-1>', activate)
    if layout == 'dock':
        canvas.create_text(26, 44, text='Z', fill='#75e6c1', font=('Segoe UI', 15, 'bold'))
        for item, x in zip([('explorer', 'Arquivos'), ('settings', 'Ajustes'), ('zeus', 'Zeus')], (81, 151, 221)):
            button(*item, x, 35)
        canvas.create_line(265, 22, 265, 70, fill='#354653')
        cx, cy = 323, 35
        close_x, close_y = 399, 45
    else:
        canvas.create_text(54, 29, text='ZEUS', fill='#75e6c1', font=('Segoe UI', 10, 'bold'))
        for item, y in zip([('explorer', 'Arquivos'), ('settings', 'Ajustes'), ('zeus', 'Zeus')], (83, 157, 231)):
            button(*item, 54, y)
        canvas.create_line(24, 281, 84, 281, fill='#354653')
        cx, cy = 54, 306
        close_x, close_y = 54, 359
    clock = canvas.create_text(cx, cy, text='', fill='#f0f6fb', font=('Segoe UI', 17), tags='clock')
    date = canvas.create_text(cx, cy+23, text='', fill='#8da4b7', font=font, tags='clock')
    canvas.tag_bind('clock', '<Button-1>', lambda _: _fixed_action('clock'))
    canvas.create_text(close_x, close_y, text='×', fill='#a0b3c2', font=('Segoe UI', 18), tags='close')
    canvas.tag_bind('close', '<Button-1>', lambda _: on_close())
    def tick():
        now = datetime.now()
        canvas.itemconfigure(clock, text=now.strftime('%H:%M'))
        canvas.itemconfigure(date, text=now.strftime('%d / %m'))
        root.after(1000, tick)
    tick()
    root.bind('<Escape>', lambda _: on_close())
    root.protocol('WM_DELETE_WINDOW', on_close)
    root.deiconify()
    root.update_idletasks()
    _native.round_own_window(root.winfo_id())
    return root


def worker_main(argv=None):
    parser = argparse.ArgumentParser(description='Zeus own-window layout worker')
    parser.add_argument('--layout-worker', required=True, choices=('dock', 'sidebar'))
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--parent-pid', required=True, type=int)
    parser.add_argument('--hide-native-taskbar', action='store_true')
    args = parser.parse_args(argv)
    if not _TOKEN.fullmatch(args.token) or args.parent_pid <= 0:
        raise ValueError('Worker inválido.')
    if not _native.supported:
        raise OSError('Layouts disponíveis apenas no Windows.')
    directory = Path(args.runtime_dir)
    with _locked(directory):
        state = _read(directory)
        if (not state or state['token'] != args.token or state['layout'] != args.layout_worker
                or state['parent_pid'] != args.parent_pid
                or state['hide_native_taskbar'] is not args.hide_native_taskbar
                or state['pid'] not in (0, os.getpid())):
            raise ValueError('Token ou estado do worker inválido.')
        # A second invocation cannot share ownership with an active worker.
        if state['state'] != 'STARTING':
            raise ValueError('Worker já iniciado ou encerrado.')
        state['pid'] = os.getpid()
        state['process_stamp'] = _native.process_stamp(os.getpid())
        _write(directory, state)
    root = None
    def close():
        nonlocal root
        if root is not None:
            window, root = root, None
            window.destroy()
    def monitor():
        stop = directory / f'{args.token}.stop'
        if stop.exists() or not _native.process_alive(args.parent_pid, state.get('parent_stamp')):
            close()
            return
        root.after(750, monitor)
    code = 0
    try:
        root = _create_window(args.layout_worker, close)
        with _locked(directory):
            if args.hide_native_taskbar:
                _hide_bars(directory, state)
            state['state'] = 'RUNNING'
            _write(directory, state)
        root.after(0, monitor)
        root.mainloop()
    except Exception as exc:
        state['error'] = f'{type(exc).__name__}: {exc}'
        code = 1
    finally:
        with _locked(directory):
            _restore_bars(directory, state)
            state['state'] = 'ERROR' if state.get('error') or state.get('restore_errors') else 'STOPPED'
            _write(directory, state)
        close()
    return code


if __name__ == '__main__':
    raise SystemExit(worker_main())
