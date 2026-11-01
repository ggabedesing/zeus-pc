"""Zeus-owned desktop widgets and launcher. Tk is loaded only on activation."""
from __future__ import annotations

import calendar
import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import threading
import time

from .environment import EnvironmentStore, ApplicationCatalog, BUILTINS, palette, widget_geometry, search_applications

WEEKDAYS = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')
MONTHS = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')


class Hotkey:
    """Registers a thread hotkey without hooks or intercepting other input."""
    def __init__(self, user=None):
        self.user = user or ctypes.WinDLL('user32', use_last_error=True)
        self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.registered = False
        self._stop, self._pressed, self._ready = threading.Event(), threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name='zeus-launcher-hotkey', daemon=True)
        self._thread.start()
        self._ready.wait(1)

    def _run(self):
        registered = False
        try:
            registered = self.registered = bool(self.user.RegisterHotKey(None, 0x5A31, 0x4002, 0x20))
            self._ready.set()
            while registered and not self._stop.wait(.04):
                message = wintypes.MSG()
                # Tk's main loop cannot consume this thread's message queue.
                if self.user.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1) and message.wParam == 0x5A31:
                    self._pressed.set()
        finally:
            if registered:
                self.user.UnregisterHotKey(None, 0x5A31)
            self.registered = False
            self._ready.set()

    def poll(self):
        pressed = self._pressed.is_set()
        self._pressed.clear()
        return pressed

    def close(self):
        self._stop.set()
        self._thread.join(timeout=.5)


class DesktopLayer:
    """Raises only our non-topmost panels when Explorer desktop has focus."""
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    def hwnd(self, window):
        child = window.winfo_id()
        return self.user.GetParent(child) or child

    def prepare(self, window):
        hwnd = self.hwnd(window)
        style = self.user.GetWindowLongPtrW(hwnd, -20)
        self.user.SetWindowLongPtrW(hwnd, -20, (style | 0x80) & ~0x40000)  # Tool window, no taskbar button.

    def desktop_focused(self):
        hwnd = self.user.GetForegroundWindow()
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, name, 256)
        return name.value in {'Progman', 'WorkerW'}

    def show_without_activation(self, window):
        # No TOPMOST flag: normal apps stay above desktop widgets.
        self.user.SetWindowPos(self.hwnd(window), None, 0, 0, 0, 0, 0x13)


def mix(first, second, ratio):
    values = [round(int(first[i:i+2], 16)*(1-ratio) + int(second[i:i+2], 16)*ratio) for i in (1, 3, 5)]
    return '#' + ''.join(f'{v:02x}' for v in values)


class Panel:
    def __init__(self, app, name, geometry):
        tk = app.tk
        self.app, self.name, self.geometry = app, name, geometry
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.title('Zeus · ' + name)
        self.window.overrideredirect(True)
        self.window.configure(bg='#010203')
        self.window.attributes('-transparentcolor', '#010203')
        self.window.attributes('-alpha', app.config['opacity'])
        x, y, self.width, self.height = geometry
        self.window.geometry(f'{self.width}x{self.height}{x:+d}{y:+d}')
        self.canvas = tk.Canvas(self.window, bg='#010203', highlightthickness=0, width=self.width, height=self.height)
        self.canvas.pack(fill='both', expand=True)
        self.scale = self.width / (600 if name == 'topbar' else (geometry[2] / (.86 if app.config['density'] == 'compact' else 1)) if name == 'dock' else 256)
        self.drag = None
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Button-3>', lambda _: app.launcher())
        self.window.bind('<Escape>', lambda _: app.launcher(hide=True))
        self.draw()
        self.window.update_idletasks()
        app.layer.prepare(self.window)
        from .layout_runtime import _native
        _native.round_own_window(self.window.winfo_id())
        self.window.deiconify()
        app.layer.show_without_activation(self.window)

    def text(self, x, y, text, size=10, color='text', anchor='w', weight='normal', tags=()):
        return self.canvas.create_text(x*self.scale, y*self.scale, text=text,
                font=('Segoe UI', max(7, round(size*self.scale)), weight), fill=self.app.colors.get(color, color), anchor=anchor, tags=tags)

    def rounded(self, x1, y1, x2, y2, radius=18, **kwargs):
        points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,x2-radius,y2,
                  x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
        return self.canvas.create_polygon([n*self.scale for n in points], smooth=True, splinesteps=24, **kwargs)

    def button(self, x, y, w, label, callback, symbol=None):
        c = self.app.colors
        tag = 'b' + str(len(self.canvas.find_all()))
        shape = self.rounded(x, y, x+w, y+40, 11, fill=mix(c['surface'], c['accent'], .08), outline='', tags=tag)
        self.text(x+w/2, y+20, symbol or label, 14 if symbol else 10, anchor='center', tags=tag)
        self.canvas.tag_bind(tag, '<Enter>', lambda _: (self.canvas.itemconfigure(shape, fill=mix(c['surface'], c['accent'], .22)), self.canvas.configure(cursor='hand2')))
        self.canvas.tag_bind(tag, '<Leave>', lambda _: (self.canvas.itemconfigure(shape, fill=mix(c['surface'], c['accent'], .08)), self.canvas.configure(cursor='')))
        def activate(_):
            callback()
            return 'break'
        self.canvas.tag_bind(tag, '<Button-1>', activate)

    def press(self, event):
        if event.y <= 34*self.scale or self.name in ('dock', 'topbar'):
            self.drag = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y(), False)

    def move(self, event):
        if not self.drag:
            return
        ax, ay, x, y, _ = self.drag
        left, top, right, bottom = self.app.area
        nx = min(max(left, x+event.x_root-ax), right-self.width)
        ny = min(max(top, y+event.y_root-ay), bottom-self.height)
        self.window.geometry(f'{nx:+d}{ny:+d}')
        self.drag = ax, ay, x, y, True

    def release(self, _):
        if self.drag and self.drag[-1]:
            left, top, right, bottom = self.app.area
            positions = self.app.store.read()['positions']
            positions[self.name] = {'x': min(1, max(0, (self.window.winfo_x()-left)/max(1, right-left-self.width))),
                                    'y': min(1, max(0, (self.window.winfo_y()-top)/max(1, bottom-top-self.height)))}
            self.app.store.update({'positions': positions})
        self.drag = None

    def draw(self):
        c, now = self.app.colors, datetime.now()
        canvas = self.canvas
        canvas.delete('all')
        w, h = self.width/self.scale, self.height/self.scale
        self.rounded(1, 1, w-1, h-1, fill=c['surface'], outline=mix(c['surface'], c['accent'], .28), width=1)
        if self.name == 'clock':
            self.text(22, 25, 'SEU TEMPO', 8, 'muted', weight='bold')
            self.text(22, 82, now.strftime('%H:%M'), 42, weight='bold')
            self.text(23, 124, WEEKDAYS[now.weekday()], 11, 'accent')
            self.text(23, 150, f'{now.day} de {MONTHS[now.month-1]}', 10, 'muted')
            self.button(202, 122, 34, '+', self.app.open_calendar)
        elif self.name == 'system':
            self.text(22, 25, 'PULSO DO PC', 8, 'muted', weight='bold')
            for index, (label, key) in enumerate([('CPU', 'cpu'), ('RAM', 'ram')]):
                y = 62+index*54; value = self.app.metrics.get(key)
                self.text(22, y, label, 10, 'muted')
                self.text(232, y, '—' if value is None else f'{value:.0f}%', 16, anchor='e', weight='bold')
                self.rounded(22, y+20, 233, y+25, 2, fill=mix(c['surface'], c['text'], .09), outline='')
                if value is not None:
                    self.rounded(22, y+20, 22+max(2, 211*value/100), y+25, 2, fill=c['accent'], outline='')
            self.text(22, 152, 'LEITURA LOCAL · 2 s', 7, 'muted')
        elif self.name == 'notes':
            self.text(22, 25, 'IDEIAS, SEM SAIR DAQUI', 8, 'muted', weight='bold')
            self.text(22, 181, 'SALVAS NESTE PC', 7, 'muted')
            if not hasattr(self, 'editor'):
                tk = self.app.tk
                self.editor = tk.Text(self.window, bg=c['surface'], fg=c['text'], insertbackground=c['accent'],
                                      relief='flat', wrap='word', font=('Segoe UI', round(10*self.scale)), undo=True)
                self.editor.insert('1.0', self.app.config['notes'])
                self.editor.place(x=22*self.scale, y=48*self.scale, width=(w-44)*self.scale, height=(h-82)*self.scale)
                self.editor.bind('<KeyRelease>', self.save_notes)
                self.editor.bind('<FocusOut>', lambda _: self.flush_notes())
                self.note_timer = None
            self.editor.configure(bg=c['surface'], fg=c['text'], insertbackground=c['accent'])
        elif self.name == 'topbar':
            self.text(22, 25, 'ϟ', 21, 'accent', weight='bold')
            self.text(53, 25, 'zeus', 13, weight='bold')
            self.text(120, 25, 'SEU ESPAÇO', 8, 'muted')
            self.text(420, 25, now.strftime('%a · %d %b'), 9, 'muted', anchor='e')
            self.button(443, 5, 147, 'Buscar aplicativos', self.app.launcher)
        elif self.name == 'dock':
            self.button(12, 15, 45, 'Buscar', self.app.launcher, '⌕')
            apps = self.app.dock_apps()
            for i, item in enumerate(apps[:max(1, int((w-112)//48))]):
                x = 68+i*48
                symbol = {'builtin:files': '▱', 'builtin:settings': '⚙', 'builtin:zeus': 'ϟ'}.get(item['id'], item['name'][:1].upper())
                self.button(x, 15, 40, item['name'], lambda id=item['id']: self.app.launch(id), symbol)
            self.text(w-19, 36, now.strftime('%H:%M'), 10, 'muted', anchor='e')
            self.text(w/2, 66, 'CTRL + ESPAÇO' if self.app.hotkey.registered else 'BUSCAR NO DOCK', 6, 'muted', anchor='center')

    def save_notes(self, _):
        if self.note_timer:
            self.app.root.after_cancel(self.note_timer)
        self.note_timer = self.app.root.after(700, self.flush_notes)

    def flush_notes(self):
        if self.name != 'notes':
            return
        self.note_timer = None
        text = self.editor.get('1.0', 'end-1c')[:4000]
        if text != self.app.store.read()['notes']:
            self.app.store.update({'notes': text})

    def close(self):
        if self.name == 'notes':
            if self.note_timer:
                self.app.root.after_cancel(self.note_timer)
            self.flush_notes()
        self.window.destroy()


class EnvironmentUI:
    def __init__(self, on_close, details):
        import tkinter as tk
        from .layout_runtime import _native
        from .observer import NativeWindowsSampler
        self.tk, self.on_close = tk, on_close
        self.root = tk.Tk(); self.root.withdraw(); self.root.title('Zeus · Ambiente')
        self.store = EnvironmentStore()
        self.config = self.store.read(); self.colors = palette(self.config)
        self.area = _native.work_area(); self.layer = DesktopLayer()
        self.hotkey = Hotkey(); details['hotkey_registered'] = self.hotkey.registered
        self.sampler = NativeWindowsSampler(); self.sampler.cpu_pct()
        self.metrics = {'cpu': None, 'ram': None}; self.last_sample = 0
        self.catalog = ApplicationCatalog(); self.apps = BUILTINS + self.catalog.list()
        self.panels, self.popup, self.calendar, self.closing = {}, None, None, False
        self.last_watch = 0
        self.rebuild()
        if not self.panels and not self.hotkey.registered:
            self.shutdown()
            self.root.destroy()
            raise OSError('O atalho já está em uso. Ative o dock ou um widget para acessar o ambiente.')
        def callback_error(kind, exception, traceback):
            details['error'] = f'{kind.__name__}: {exception}'
            on_close()
        self.root.report_callback_exception = callback_error
        self.root.protocol('WM_DELETE_WINDOW', on_close)
        self.root.bind('<Destroy>', self.destroyed, add='+')
        self.root.after(150, self.tick)

    def dock_apps(self):
        by_id = {item['id']: item for item in self.apps}
        ids = ['builtin:files', 'builtin:settings', 'builtin:zeus'] + self.config['pinned']
        return [by_id[id] for id in dict.fromkeys(ids) if id in by_id][:11]

    def rebuild(self):
        for panel in self.panels.values():
            panel.close()
        self.panels = {}
        for name, geometry in widget_geometry(self.config, self.area).items():
            if self.config['widgets'][name]:
                self.panels[name] = Panel(self, name, geometry)

    def launch(self, id):
        self.launcher(hide=True)
        try:
            if id.startswith('builtin:'):
                from .layout_runtime import _fixed_action
                action = {'builtin:files': 'explorer', 'builtin:settings': 'settings', 'builtin:zeus': 'zeus'}[id]
                _fixed_action(action)
            else:
                self.catalog.launch(id)
        except (OSError, ValueError, KeyError):
            self.launcher()
            self.status.configure(text='O aplicativo não está disponível. Atualize a busca.')

    def fade(self, window, start=0):
        if self.closing or not window.winfo_exists():
            return
        target = self.config['opacity']
        current = min(target, start+.17)
        window.attributes('-alpha', current if self.config['motion'] else target)
        if current < target and self.config['motion']:
            self.root.after(16, lambda: self.fade(window, current))

    def launcher(self, hide=False):
        if self.popup is not None:
            self.popup.destroy(); self.popup = None
            if not hide:
                return
        if hide or self.closing:
            return
        tk, c = self.tk, self.colors
        popup = self.popup = tk.Toplevel(self.root)
        popup.withdraw(); popup.overrideredirect(True); popup.title('Zeus · Buscar')
        popup.attributes('-topmost', True); popup.attributes('-alpha', 0 if self.config['motion'] else self.config['opacity'])
        popup.configure(bg=c['surface'], padx=22, pady=18)
        left, top, right, bottom = self.area
        width, height = min(610, right-left-32), min(510, bottom-top-32)
        popup.geometry(f'{width}x{height}{left+(right-left-width)//2:+d}{top+max(16, (bottom-top-height)//3):+d}')
        tk.Label(popup, text='O que vamos abrir?', bg=c['surface'], fg=c['text'], font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        tk.Label(popup, text='Aplicativos deste PC', bg=c['surface'], fg=c['muted'], font=('Segoe UI', 10)).pack(anchor='w', pady=(2, 14))
        self.query = tk.StringVar()
        entry = tk.Entry(popup, textvariable=self.query, bg=mix(c['surface'], c['accent'], .08), fg=c['text'],
                         insertbackground=c['accent'], relief='flat', font=('Segoe UI', 15))
        entry.pack(fill='x', ipady=12)
        self.results = tk.Listbox(popup, bg=c['surface'], fg=c['text'], selectbackground=mix(c['surface'], c['accent'], .22),
                                 selectforeground=c['text'], relief='flat', highlightthickness=0, activestyle='none',
                                 font=('Segoe UI', 12), height=8, exportselection=False)
        self.results.pack(fill='both', expand=True, pady=12)
        self.status = tk.Label(popup, text='↑ ↓ escolher     Enter abrir     Esc fechar', bg=c['surface'], fg=c['muted'], font=('Segoe UI', 9))
        self.status.pack(anchor='w')
        self.apps = BUILTINS + self.catalog.list(refresh=True)
        self.query.trace_add('write', lambda *_: self.filter_apps())
        self.filter_apps()
        entry.bind('<Down>', lambda _: self.navigate(1))
        entry.bind('<Up>', lambda _: self.navigate(-1))
        popup.bind('<Escape>', lambda _: self.launcher(hide=True))
        popup.bind('<Return>', self.open_selected)
        self.results.bind('<Double-Button-1>', self.open_selected)
        # No FocusOut auto-close: selecting a list or changing an input method is valid.
        popup.update_idletasks(); self.layer.prepare(popup)
        from .layout_runtime import _native
        _native.round_own_window(popup.winfo_id())
        popup.deiconify(); popup.lift(); entry.focus_force(); self.fade(popup)

    def filter_apps(self):
        self.matches = search_applications(self.apps, self.query.get())
        self.results.delete(0, 'end')
        for item in self.matches:
            self.results.insert('end', '  ' + item['name'])
        if self.matches:
            self.results.selection_set(0)
        self.status.configure(text='↑ ↓ escolher     Enter abrir     Esc fechar' if self.matches else 'Nenhum aplicativo encontrado no Menu Iniciar.')

    def navigate(self, step):
        count = len(self.matches)
        if count:
            selected = self.results.curselection()
            index = min(count-1, max(0, (selected[0] if selected else 0)+step))
            self.results.selection_clear(0, 'end'); self.results.selection_set(index); self.results.see(index)
        return 'break'

    def open_selected(self, _=None):
        selected = self.results.curselection()
        if selected and selected[0] < len(self.matches):
            self.launch(self.matches[selected[0]]['id'])
        return 'break'

    def open_calendar(self):
        if self.calendar is not None:
            self.calendar.destroy(); self.calendar = None
            return
        tk, c, now = self.tk, self.colors, datetime.now()
        win = self.calendar = tk.Toplevel(self.root)
        win.title('Zeus · Calendário'); win.configure(bg=c['surface']); win.resizable(False, False)
        win.attributes('-topmost', True)
        left, top, _, _ = self.area
        win.geometry(f'310x310{left+40:+d}{top+80:+d}')
        def closed():
            win.destroy(); self.calendar = None
        win.protocol('WM_DELETE_WINDOW', closed); win.bind('<Escape>', lambda _: closed())
        title = tk.Label(win, text=f'{MONTHS[now.month-1].capitalize()} {now.year}', bg=c['surface'], fg=c['text'], font=('Segoe UI', 17, 'bold'))
        title.pack(pady=(22, 14))
        grid = tk.Frame(win, bg=c['surface']); grid.pack()
        for col, day in enumerate(['S', 'T', 'Q', 'Q', 'S', 'S', 'D']):
            tk.Label(grid, text=day, bg=c['surface'], fg=c['muted'], width=4).grid(row=0, column=col, pady=6)
        for row, week in enumerate(calendar.monthcalendar(now.year, now.month), 1):
            for col, day in enumerate(week):
                current = day == now.day
                tk.Label(grid, text=str(day) if day else '', bg=c['accent'] if current else c['surface'],
                         fg=c['background'] if current else c['text'], width=4, font=('Segoe UI', 10)).grid(row=row, column=col, pady=3)

    def tick(self):
        if self.closing:
            return
        if self.hotkey.poll():
            self.launcher()
        now = time.monotonic()
        if now-self.last_watch >= 1:
            self.last_watch = now
            latest = self.store.read()
            from .layout_runtime import _native
            area = _native.work_area()
            if latest['revision'] != self.config['revision'] or area != self.area:
                structural = lambda config: {key: value for key, value in config.items() if key not in ('notes', 'revision')}
                changed = structural(latest) != structural(self.config) or area != self.area
                if changed:
                    for panel in self.panels.values():
                        if panel.name == 'notes':
                            panel.flush_notes()
                self.config = self.store.read(); self.colors = palette(self.config); self.area = area
                if changed:
                    self.launcher(hide=True)
                    if self.calendar is not None:
                        self.calendar.destroy(); self.calendar = None
                    self.rebuild()
                elif (panel := self.panels.get('notes')) and self.root.focus_get() is not panel.editor:
                    if panel.editor.get('1.0', 'end-1c') != self.config['notes']:
                        panel.editor.delete('1.0', 'end'); panel.editor.insert('1.0', self.config['notes'])
            if now-self.last_sample >= 2 and self.config['widgets']['system']:
                self.last_sample = now
                self.metrics = {'cpu': self.sampler.cpu_pct(), 'ram': self.sampler.ram_used_pct()}
            desktop = self.layer.desktop_focused()
            for panel in self.panels.values():
                if panel.name != 'notes':
                    panel.draw()
                if desktop:
                    self.layer.show_without_activation(panel.window)
        self.root.after(120, self.tick)

    def destroyed(self, event):
        if event.widget is not self.root or self.closing:
            return
        self.closing = True
        self.hotkey.close()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        try:
            for panel in self.panels.values():
                if panel.name == 'notes':
                    panel.flush_notes()
        finally:
            self.hotkey.close()
            for timer in self.root.tk.call('after', 'info'):
                self.root.after_cancel(timer)


def create_environment(on_close, details):
    app = None
    try:
        app = EnvironmentUI(on_close, details)
        app.root._zeus_environment = app  # Preserve callbacks for the whole worker lifetime.
        return app.root
    except Exception:
        if app is not None:
            app.root.destroy()
        raise
