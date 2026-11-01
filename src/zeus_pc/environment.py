"""Local desktop configuration, layout and trusted Start Menu applications.

Reading this module never creates windows, launches apps or changes Windows.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import unicodedata

from .app_paths import data_dir

THEMES = {
    'aurora': dict(name='Aurora', background='#071b24', surface='#142c37', accent='#72e9c7', text='#edfff9', muted='#9cbcb9'),
    'graphite': dict(name='Grafite', background='#101319', surface='#202630', accent='#8da8c8', text='#f0f4fa', muted='#9caabb'),
    'light': dict(name='Luz', background='#dce8f3', surface='#f6f9fc', accent='#5077bd', text='#172b44', muted='#60748c'),
    'minimal': dict(name='Essencial', background='#141619', surface='#202327', accent='#acb7ba', text='#edf0f1', muted='#929b9e'),
}
WIDGETS = ('clock', 'system', 'notes', 'dock', 'topbar')
BUILTINS = [dict(id='builtin:files', name='Arquivos', source='Windows'),
            dict(id='builtin:settings', name='Configurações', source='Windows'),
            dict(id='builtin:zeus', name='Painel Zeus', source='Zeus')]
_ID = re.compile(r'^[0-9a-f]{64}$')
_LOCK = threading.RLock()


def defaults():
    return dict(theme='aurora', accent=None, motion=True, opacity=.97,
                density='comfortable', widgets=dict(clock=True, system=True, notes=False, dock=True, topbar=True),
                positions={}, pinned=[], notes='', revision=0)


def validate(config):
    allowed = set(defaults())
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Configuração de ambiente inválida.')
    result = copy.deepcopy(config)
    if config.get('theme', 'aurora') not in THEMES:
        raise ValueError('Tema de ambiente inválido.')
    if 'accent' in config and config['accent'] is not None and (not isinstance(config['accent'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', config['accent'])):
        raise ValueError('Escolha uma cor hexadecimal válida.')
    if 'motion' in config and type(config['motion']) is not bool:
        raise ValueError('A opção de animações deve ser booleana.')
    if 'opacity' in config and (type(config['opacity']) not in (int, float) or not math.isfinite(config['opacity']) or not .8 <= config['opacity'] <= 1):
        raise ValueError('Opacidade deve estar entre 0.8 e 1.')
    if config.get('density', 'comfortable') not in ('comfortable', 'compact'):
        raise ValueError('Densidade inválida.')
    if 'notes' in config and (not isinstance(config['notes'], str) or len(config['notes']) > 4000):
        raise ValueError('As notas aceitam até 4000 caracteres.')
    if 'revision' in config and (type(config['revision']) is not int or config['revision'] < 0):
        raise ValueError('Revisão inválida.')
    if 'widgets' in config:
        widgets = config['widgets']
        if not isinstance(widgets, dict) or set(widgets) - set(WIDGETS) or any(type(v) is not bool for v in widgets.values()):
            raise ValueError('Widgets inválidos.')
    if 'positions' in config:
        positions = config['positions']
        if not isinstance(positions, dict) or set(positions) - set(WIDGETS):
            raise ValueError('Posições inválidas.')
        for position in positions.values():
            if (not isinstance(position, dict) or set(position) != {'x', 'y'} or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in position.values())):
                raise ValueError('Posição deve estar dentro da área de trabalho.')
    if 'pinned' in config:
        pinned = config['pinned']
        if (not isinstance(pinned, list) or len(pinned) > 8 or len(set(str(v) for v in pinned)) != len(pinned)
            or any(not isinstance(v, str) or not (_ID.fullmatch(v) or v in {b['id'] for b in BUILTINS}) for v in pinned)):
            raise ValueError('Escolha até oito aplicativos do catálogo.')
    return result


@contextmanager
def _locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK, (directory / 'config.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class EnvironmentStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else data_dir() / 'environment'

    def _read(self):
        path = self.directory / 'config.json'
        result = defaults()
        if not path.exists():
            return result
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('Arquivo de ambiente inválido.')
        try:
            saved = validate(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError, TypeError) as exc:
            raise ValueError('Não foi possível ler o ambiente salvo. Preserve config.json para recuperação.') from exc
        for key, value in saved.items():
            if key == 'widgets':
                result['widgets'].update(value)
            else:
                result[key] = value
        return result

    def read(self):
        # No file or lock is created by a read of a fresh installation.
        if not self.directory.exists():
            return defaults()
        with _locked(self.directory):
            return self._read()

    def update(self, changes):
        changes = validate(changes)
        if 'revision' in changes:
            raise ValueError('A revisão é controlada pelo Zeus.')
        with _locked(self.directory):
            config = self._read()
            for key, value in changes.items():
                if key == 'widgets':
                    config[key].update(value)
                else:
                    config[key] = value
            config['revision'] = max(time.time_ns(), config['revision'] + 1)
            temporary = self.directory / 'config.tmp'
            if temporary.is_symlink():
                raise ValueError('Arquivo de configuração inválido.')
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump(config, handle, ensure_ascii=False, allow_nan=False)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.directory / 'config.json')
            return copy.deepcopy(config)


def palette(config):
    colors = dict(THEMES[config['theme']])
    colors['accent'] = config['accent'] or colors['accent']
    return colors


def widget_geometry(config, area):
    left, top, right, bottom = area
    aw, ah = max(1, right-left), max(1, bottom-top)
    scale = .86 if config['density'] == 'compact' else 1
    sizes = {'clock': (256, 184), 'system': (256, 172), 'notes': (256, 202),
             'dock': (min(760, 272 + len(config['pinned']) * 48), 78), 'topbar': (600, 50)}
    positions = dict(clock=(.025, .12), system=(.975, .12), notes=(.975, .72), dock=(.5, .97), topbar=(.5, .025))
    result = {}
    for name, (width, height) in sizes.items():
        width, height = min(aw, round(width*scale)), min(ah, round(height*scale))
        pos = config['positions'].get(name)
        px, py = (pos['x'], pos['y']) if pos else positions[name]
        result[name] = (left + round((aw-width)*px), top + round((ah-height)*py), width, height)
    return result


def normalize_query(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold()) if not unicodedata.combining(c))


def search_applications(items, query, limit=12):
    words = normalize_query(query).split()
    matched = [item for item in items if all(word in normalize_query(item['name']) for word in words)]
    return sorted(matched, key=lambda item: (not normalize_query(item['name']).startswith(' '.join(words)), normalize_query(item['name'])))[:limit]


def _open_file(path):
    os.startfile(path)


class ApplicationCatalog:
    def __init__(self, roots=None):
        if roots is None:
            roots = [Path(base) / 'Microsoft/Windows/Start Menu/Programs'
                     for name in ('APPDATA', 'PROGRAMDATA') if (base := os.environ.get(name))]
        self.roots = [Path(root).resolve() for root in roots]
        self._items, self._paths, self._at = [], {}, 0

    def list(self, refresh=False):
        if not refresh and time.monotonic() - self._at < 20:
            return copy.deepcopy(self._items)
        paths, names, items = {}, set(), []
        for root in self.roots:
            if not root.is_dir():
                continue
            for directory, subdirs, files in os.walk(root, followlinks=False):
                here = Path(directory)
                subdirs[:] = [name for name in sorted(subdirs) if not (here/name).is_symlink()]
                if len(here.relative_to(root).parts) >= 5:
                    subdirs[:] = []
                for name in sorted(files):
                    path = here / name
                    if path.suffix.casefold() != '.lnk' or path.is_symlink():
                        continue
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root) or not resolved.is_file():
                        continue
                    # Duplicate Start Menu names collapse to the first root.
                    key = normalize_query(path.stem)
                    if key in names:
                        continue
                    id = hashlib.sha256(str(resolved).casefold().encode()).hexdigest()
                    paths[id] = resolved; names.add(key)
                    items.append(dict(id=id, name=path.stem, source='Menu Iniciar'))
                    if len(items) >= 1000:
                        break
                if len(items) >= 1000:
                    break
        self._items = sorted(items, key=lambda item: normalize_query(item['name']))
        self._paths, self._at = paths, time.monotonic()
        return copy.deepcopy(self._items)

    def launch(self, id):
        self.list(refresh=True)
        path = self._paths.get(id)
        if path is None or not path.is_file() or path.is_symlink():
            raise ValueError('Aplicativo não está mais no Menu Iniciar.')
        _open_file(str(path))
