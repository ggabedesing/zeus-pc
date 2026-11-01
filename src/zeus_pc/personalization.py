"""Managed wallpapers and reversible, current-user Windows visual settings.

Importing this module never probes or changes Windows. Native writes occur only
through registered transaction apply/rollback functions, never catalog/state reads.
"""
from __future__ import annotations

import base64
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import struct
import threading
import zlib

from .app_paths import data_dir as _data_dir

PERSONALIZE = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
DESKTOP = r'Control Panel\Desktop'
WINDOW_METRICS = r'Control Panel\Desktop\WindowMetrics'
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_ASSET_ID = re.compile(r'^[0-9a-f]{64}$')
_SNAPSHOT_ID = re.compile(r'^[0-9a-f]{32}$')
_LOCK = threading.RLock()
_ANIMATIONS = {
    'client_area': (0x1042, 0x1043), 'menu': (0x1002, 0x1003),
    'menu_fade': (0x1012, 0x1013), 'tooltip': (0x1016, 0x1017),
    'tooltip_fade': (0x1018, 0x1019), 'selection_fade': (0x1014, 0x1015),
}
_WALLPAPER_VALUES = [(DESKTOP, name) for name in ('Wallpaper', 'WallpaperStyle', 'TileWallpaper')]
_VISUAL_VALUES = (_WALLPAPER_VALUES + [(PERSONALIZE, name) for name in (
    'AppsUseLightTheme', 'SystemUsesLightTheme', 'EnableTransparency')]
    + [(DESKTOP, 'UserPreferencesMask'), (WINDOW_METRICS, 'MinAnimate')])
_PRESETS = (
    dict(id='graphite', name='Grafite', description='Escuro, discreto e com movimento suave.',
         theme='dark', transparency=True, motion=True,
         colors=dict(background='#101319', surface='#202630', accent='#8da8c8', text='#f0f4fa')),
    dict(id='aurora', name='Aurora', description='Luzes verdes e violetas em um desktop escuro.',
         theme='dark', transparency=True, motion=True,
         colors=dict(background='#071b24', surface='#142c37', accent='#72e9c7', text='#edfff9')),
    dict(id='light', name='Luz', description='Superfícies claras e um horizonte azul suave.',
         theme='light', transparency=True, motion=True,
         colors=dict(background='#dce8f3', surface='#f6f9fc', accent='#5077bd', text='#172b44')),
    dict(id='minimal', name='Essencial', description='Escuro, opaco e com menos movimento.',
         theme='dark', transparency=False, motion=False,
         colors=dict(background='#141619', surface='#202327', accent='#acb7ba', text='#edf0f1')),
)


def _error(message):
    from .transaction import TransactionError
    return TransactionError(message)


class _AnimationInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('iMinAnimate', ctypes.c_int)]


class NativeWindowsAdapter:
    supported = os.name == 'nt'

    @staticmethod
    def _user32():
        if os.name != 'nt':
            raise OSError('Personalização disponível apenas no Windows.')
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        return user32

    def _spi(self, operation, param=0, value=None, flags=0):
        if not self._user32().SystemParametersInfoW(operation, param, value, flags):
            raise ctypes.WinError(ctypes.get_last_error())

    def read_registry(self, path, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return {'exists': False}
        if isinstance(value, bytes):
            value = {'base64': base64.b64encode(value).decode('ascii')}
        return {'exists': True, 'type': kind, 'value': value}

    def write_registry(self, path, name, state):
        import winreg
        if not state['exists']:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
            return
        value = state['value']
        if isinstance(value, dict) and set(value) == {'base64'}:
            value = base64.b64decode(value['base64'], validate=True)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, state['type'], value)

    def read_animations(self):
        result = {}
        for name, (get, _) in _ANIMATIONS.items():
            value = wintypes.BOOL()
            self._spi(get, value=ctypes.byref(value))
            result[name] = bool(value.value)
        info = _AnimationInfo(ctypes.sizeof(_AnimationInfo), 0)
        self._spi(0x0048, ctypes.sizeof(info), ctypes.byref(info))
        result['minimize_restore'] = bool(info.iMinAnimate)
        return result

    def write_animations(self, values):
        # Fade flags are ignored when their master animation is off. Set them
        # while enabled, then restore the requested master states last.
        for name in ('menu', 'tooltip'):
            self._spi(_ANIMATIONS[name][1], value=ctypes.c_void_p(1), flags=3)
        for name, (_, set_) in _ANIMATIONS.items():
            if name in ('menu', 'tooltip'):
                continue
            self._spi(set_, value=ctypes.c_void_p(int(values[name])), flags=3)
        for name in ('menu', 'tooltip'):
            self._spi(_ANIMATIONS[name][1], value=ctypes.c_void_p(int(values[name])), flags=3)
        info = _AnimationInfo(ctypes.sizeof(_AnimationInfo), int(values['minimize_restore']))
        self._spi(0x0049, ctypes.sizeof(info), ctypes.byref(info), 3)

    def read_wallpaper(self):
        # Current SPI path may exceed historical MAX_PATH on modern Windows.
        buffer = ctypes.create_unicode_buffer(32768)
        self._spi(0x0073, len(buffer), buffer)
        return buffer.value

    def write_wallpaper(self, path):
        # Empty string is valid: it restores a previous solid-color desktop.
        self._spi(0x0014, value=ctypes.c_wchar_p(path), flags=3)

    def notify(self):
        user32 = self._user32()
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                             wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                             ctypes.POINTER(ctypes.c_size_t)]
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        result = ctypes.c_size_t()
        area = ctypes.create_unicode_buffer('ImmersiveColorSet')
        # Bounded broadcast; unresponsive applications cannot hang the UI.
        user32.SendMessageTimeoutW(0xffff, 0x001a, 0, ctypes.cast(area, ctypes.c_void_p).value,
                                  0x0002, 200, ctypes.byref(result))


_adapter = NativeWindowsAdapter()


def _assets_dir():
    directory = _data_dir() / 'wallpapers'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _image_info(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Envie uma imagem PNG, JPEG ou BMP de até 20 MiB.')
    width = height = 0
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if (len(data) < 45 or data[8:16] != b'\0\0\0\rIHDR'
                or zlib.crc32(data[12:29]) & 0xffffffff != struct.unpack('>I', data[29:33])[0]
                or not data.endswith(b'\0\0\0\0IEND\xaeB`\x82')):
            raise ValueError('PNG truncado ou cabeçalho inválido.')
        width, height = struct.unpack('>II', data[16:24])
        extension = 'png'
    elif data.startswith(b'BM'):
        if len(data) < 54 or struct.unpack_from('<I', data, 2)[0] != len(data):
            raise ValueError('BMP truncado ou inválido.')
        header = struct.unpack_from('<I', data, 14)[0]
        width, height = struct.unpack_from('<ii', data, 18)
        planes, depth = struct.unpack_from('<HH', data, 26)
        compression = struct.unpack_from('<I', data, 30)[0]
        offset = struct.unpack_from('<I', data, 10)[0]
        height = abs(height)
        if (header < 40 or planes != 1 or depth not in (24, 32) or compression != 0
                or offset < 14 + header or offset + ((width * depth + 31) // 32 * 4) * height > len(data)):
            raise ValueError('Use um BMP RGB de 24 ou 32 bits sem compressão.')
        extension = 'bmp'
    elif data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xff:
                break
            while offset < len(data) and data[offset] == 0xff:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
                continue
            if marker in (0xd9, 0xda) or offset + 2 > len(data):
                break
            size = struct.unpack_from('>H', data, offset)[0]
            if size < 2 or offset + size > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2) and size >= 8:
                height, width = struct.unpack_from('>HH', data, offset + 3)
                break
            offset += size
        extension = 'jpg'
    else:
        raise ValueError('Formato inválido. Use PNG, JPEG ou BMP.')
    if not (1 <= width <= 16384 and 1 <= height <= 16384 and width * height <= 100_000_000):
        raise ValueError('Dimensões inválidas ou imagem grande demais.')
    return {'format': extension, 'width': width, 'height': height, 'bytes': len(data)}


def import_wallpaper(data: bytes) -> dict:
    """Import bytes, never a client path; immutable SHA-256 asset identifiers."""
    info = _image_info(data)
    asset_id = hashlib.sha256(data).hexdigest()
    path = _assets_dir() / f'{asset_id}.{info["format"]}'
    with _LOCK:
        if path.is_symlink():
            raise ValueError('Asset inválido.')
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Asset existente foi modificado.')
        if not path.exists():
            with path.open('xb') as file:
                file.write(data)
    return dict(id=asset_id, name='Imagem importada', **info)


def wallpaper_asset(asset_id: str) -> Path:
    """Resolve only a managed, signature-checked asset; suitable for HTTP serving."""
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        raise ValueError('ID de wallpaper inválido.')
    directory = _assets_dir().resolve()
    for extension in ('bmp', 'png', 'jpg'):
        path = directory / f'{asset_id}.{extension}'
        if not path.exists():
            continue
        if path.is_symlink() or path.resolve().parent != directory or path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('Asset inválido.')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset_id or _image_info(data)['format'] != extension:
            raise ValueError('O wallpaper foi modificado; importe novamente.')
        return path
    raise ValueError('Wallpaper não encontrado; importe novamente.')


def _gradient_bmp(preset):
    width, height = 960, 540
    colors = preset['colors']
    base = tuple(bytes.fromhex(colors['background'][1:]))
    accent = tuple(bytes.fromhex(colors['accent'][1:]))
    surface = tuple(bytes.fromhex(colors['surface'][1:]))
    pixels = bytearray(width * height * 3)
    # Quiet left side for desktop icons, luminous ribbons across the right side.
    for y in range(height):
        fy = y / height
        center = .47 + .19 * math.sin(fy * 5.4)
        for x in range(width):
            fx = x / width
            glow = math.exp(-((fx - .77) ** 2 / .16 + (fy - .39) ** 2 / .24))
            ribbon = math.exp(-((fx - center) ** 2) / .003) * .12 * fx
            blend = min(.55, glow * .34 + ribbon)
            shade = fy * .16
            index = ((height - y - 1) * width + x) * 3
            for channel in range(3):
                pixels[index + 2 - channel] = round(base[channel] * (1 - blend - shade)
                                                    + accent[channel] * blend + surface[channel] * shade)
    return (b'BM' + struct.pack('<IHHI', len(pixels) + 54, 0, 0, 54)
            + struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
            + pixels)


def _builtin_wallpaper(preset):
    # The manifest avoids regenerating ~2 MiB bitmaps on each catalog request.
    manifest = _assets_dir() / f'builtin-v1-{preset["id"]}.json'
    with _LOCK:
        if manifest.is_file() and not manifest.is_symlink():
            try:
                info = json.loads(manifest.read_text(encoding='utf-8'))
                wallpaper_asset(info['id'])
                return info
            except (ValueError, OSError, KeyError):
                pass
        info = import_wallpaper(_gradient_bmp(preset))
        info.update(name=preset['name'], builtin=True)
        manifest.write_text(json.dumps(info), encoding='utf-8')
        return info


def catalog() -> dict:
    presets, wallpapers = [], []
    for original in _PRESETS:
        preset = copy.deepcopy(original)
        wallpaper = _builtin_wallpaper(preset)
        preset['wallpaper_id'] = wallpaper['id']
        preset['animations'] = _motion(preset['motion'])
        presets.append(preset)
        wallpapers.append(wallpaper)
    known = {item['id'] for item in wallpapers}
    for path in _assets_dir().iterdir():
        if path.stem in known or not _ASSET_ID.fullmatch(path.stem):
            continue
        try:
            valid = wallpaper_asset(path.stem)
            wallpapers.append(dict(id=path.stem, name='Imagem importada', builtin=False,
                                   **_image_info(valid.read_bytes())))
            known.add(path.stem)
        except (OSError, ValueError):
            continue
    return {'presets': presets, 'wallpapers': wallpapers, 'capabilities': {
        'supported': _adapter.supported, 'desktop_wallpaper': True,
        'theme': True, 'native_transparency': True, 'animation_controls': True,
        'lock_screen_wallpaper': False, 'credential_layout': False,
        'custom_taskbar_layout': False,
        'note': 'Tema claro/escuro, translucidez nativa e efeitos do Windows. A aparência depende da versão e dos apps.',
    }}


def _motion(enabled):
    return {name: enabled for name in (*_ANIMATIONS, 'minimize_restore')}


def current_state() -> dict:
    state = dict(supported=_adapter.supported, theme=None, transparency=None,
                 animations={}, wallpaper=None, read_errors=[])
    if not _adapter.supported:
        return state
    try:
        app = _adapter.read_registry(PERSONALIZE, 'AppsUseLightTheme')
        system = _adapter.read_registry(PERSONALIZE, 'SystemUsesLightTheme')
        light = [item.get('value') if item.get('exists') else None for item in (app, system)]
        state['theme'] = 'light' if light == [1, 1] else 'dark' if light == [0, 0] else 'mixed'
        transparency = _adapter.read_registry(PERSONALIZE, 'EnableTransparency')
        state['transparency'] = bool(transparency['value']) if transparency.get('type') == 4 else None
        state['registry'] = dict(app_theme=app, system_theme=system, transparency=transparency)
        state['animations'] = _adapter.read_animations()
        state['wallpaper'] = _adapter.read_wallpaper()
    except OSError as exc:
        state['read_errors'].append(str(exc))
    return state


def _preset(args):
    if not isinstance(args, dict) or set(args) != {'preset_id'} or not isinstance(args['preset_id'], str):
        raise _error('Informe apenas preset_id.')
    preset = next((x for x in _PRESETS if x['id'] == args['preset_id']), None)
    if preset is None:
        raise _error('Preset visual desconhecido.')
    return preset


def _validate_preset(args):
    _preset(args)
    return dict(args)


def _validate_wallpaper(args):
    if not isinstance(args, dict) or set(args) != {'asset_id'}:
        raise _error('Informe apenas asset_id; caminhos não são aceitos.')
    try:
        wallpaper_asset(args['asset_id'])
    except (ValueError, OSError) as exc:
        raise _error(str(exc)) from exc
    return dict(args)


def _capture(kind):
    if not _adapter.supported:
        raise OSError('Personalização disponível apenas no Windows.')
    values = _VISUAL_VALUES if kind == 'visual' else _WALLPAPER_VALUES
    state = {'kind': kind, 'registry': [dict(path=path, name=name,
              state=_adapter.read_registry(path, name)) for path, name in values],
             'wallpaper': _adapter.read_wallpaper()}
    if kind == 'visual':
        state['animations'] = _adapter.read_animations()
    snapshot_id = secrets.token_hex(16)
    directory = _data_dir() / 'personalization-snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f'{snapshot_id}.json').open('x', encoding='utf-8') as file:
        json.dump(state, file, ensure_ascii=False)
    state['snapshot_id'] = snapshot_id
    return state


def _load_snapshot(args, kind):
    if (not isinstance(args, dict) or set(args) != {'snapshot_id'}
            or not isinstance(args['snapshot_id'], str) or not _SNAPSHOT_ID.fullmatch(args['snapshot_id'])):
        raise _error('Snapshot inválido.')
    directory = (_data_dir() / 'personalization-snapshots').resolve()
    path = directory / f'{args["snapshot_id"]}.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise _error('Snapshot não encontrado.')
    state = json.loads(path.read_text(encoding='utf-8'))
    values = _VISUAL_VALUES if kind == 'visual' else _WALLPAPER_VALUES
    if (state.get('kind') != kind or [(x['path'], x['name']) for x in state['registry']] != values
            or not isinstance(state.get('wallpaper'), str)):
        raise _error('Snapshot incompatível.')
    return state


def _preview(before):
    return {'action_id': 'RESTORE_VISUAL_STATE' if before['kind'] == 'visual' else 'RESTORE_DESKTOP_WALLPAPER',
            'args': {'snapshot_id': before['snapshot_id']}}


def _restore(before):
    # Restore live SPI state first, then exact registry types/absence. SPI itself
    # persists preferences; reversing that order would recreate absent values.
    if before['kind'] == 'visual':
        _adapter.write_animations(before['animations'])
    _adapter.write_wallpaper(before['wallpaper'])
    for value in before['registry']:
        _adapter.write_registry(value['path'], value['name'], value['state'])
    _adapter.notify()
    return _verify_snapshot(before)


def _verify_snapshot(before):
    mismatch = [value['name'] for value in before['registry']
                if _adapter.read_registry(value['path'], value['name']) != value['state']]
    if _adapter.read_wallpaper() != before['wallpaper']:
        mismatch.append('wallpaper')
    if before['kind'] == 'visual' and _adapter.read_animations() != before['animations']:
        mismatch.append('animations')
    return {'ok': not mismatch, 'mismatches': mismatch, 'source': 'HKCU + SystemParametersInfoW'}


def _guarded(kind, mutate):
    with _LOCK:
        before = _capture(kind)
        try:
            result = mutate()
            if isinstance(result, dict) and not result.get('ok'):
                raise OSError('A restauração não confirmou o estado esperado.')
            return {'ok': True}
        except Exception as exc:
            try:
                rollback = _restore(before)
            except Exception as rollback_exc:
                rollback = {'ok': False, 'message': str(rollback_exc)}
            return {'ok': False, 'message': str(exc), 'restored': rollback['ok'], 'rollback': rollback}


def _set_wallpaper(asset_id):
    path = wallpaper_asset(asset_id)
    for name, value in [('WallpaperStyle', '10'), ('TileWallpaper', '0')]:
        _adapter.write_registry(DESKTOP, name, {'exists': True, 'type': 1, 'value': value})
    _adapter.write_wallpaper(str(path))


def _apply_preset(args):
    preset = _preset(args)
    wallpaper = _builtin_wallpaper(preset)
    def mutate():
        for name in ('AppsUseLightTheme', 'SystemUsesLightTheme', 'EnableTransparency'):
            value = preset['transparency'] if name == 'EnableTransparency' else preset['theme'] == 'light'
            _adapter.write_registry(PERSONALIZE, name, {'exists': True, 'type': 4, 'value': int(value)})
        _adapter.write_animations(_motion(preset['motion']))
        _set_wallpaper(wallpaper['id'])
        _adapter.notify()
    return _guarded('visual', mutate)


def _verify_wallpaper(args):
    path = str(wallpaper_asset(args['asset_id']))
    actual = _adapter.read_wallpaper()
    styles = [_adapter.read_registry(DESKTOP, name) for name in ('WallpaperStyle', 'TileWallpaper')]
    return {'ok': os.path.normcase(actual) == os.path.normcase(path)
            and styles == [{'exists': True, 'type': 1, 'value': '10'}, {'exists': True, 'type': 1, 'value': '0'}],
            'wallpaper': actual, 'source': 'SystemParametersInfoW + HKCU'}


def _verify_preset(args):
    preset = _preset(args)
    wanted = [int(preset['theme'] == 'light')] * 2 + [int(preset['transparency'])]
    actual = [_adapter.read_registry(PERSONALIZE, name) for name in (
        'AppsUseLightTheme', 'SystemUsesLightTheme', 'EnableTransparency')]
    ok = actual == [{'exists': True, 'type': 4, 'value': x} for x in wanted]
    ok = ok and _adapter.read_animations() == _motion(preset['motion'])
    wallpaper = _builtin_wallpaper(preset)
    ok = ok and _verify_wallpaper({'asset_id': wallpaper['id']})['ok']
    return {'ok': ok, 'preset_id': preset['id'], 'source': 'HKCU + SystemParametersInfoW',
            'note': 'Preferências verificadas; apps e políticas do Windows podem limitar a aparência.'}


def register_actions(registry):
    """Attach to transaction.build_default_registry without import cycles."""
    from .transaction import ActionDefinition
    def schema(name):
        return {'type': 'object', 'properties': {name: {'type': 'string'}},
                'required': [name], 'additionalProperties': False}
    def register(action_id, kind, validate, apply, verify, argument):
        registry.register(ActionDefinition(
            action_id=action_id, resource_key='WINDOWS_PERSONALIZATION', required_privilege='USER',
            reversible=True, validate_args=validate, capture_before=lambda _: _capture(kind),
            verify=verify, rollback_preview=_preview, apply=apply,
            rollback=lambda before, _: _restore(before), arg_schema=schema(argument)))
    register('SET_VISUAL_PRESET', 'visual', _validate_preset, _apply_preset, _verify_preset, 'preset_id')
    register('SET_DESKTOP_WALLPAPER', 'wallpaper', _validate_wallpaper,
             lambda args: _guarded('wallpaper', lambda: _set_wallpaper(args['asset_id'])),
             _verify_wallpaper, 'asset_id')
    for action_id, kind in [('RESTORE_VISUAL_STATE', 'visual'), ('RESTORE_DESKTOP_WALLPAPER', 'wallpaper')]:
        def validate(args, kind=kind):
            _load_snapshot(args, kind)
            return dict(args)
        def apply(args, kind=kind):
            state = _load_snapshot(args, kind)
            result = _guarded(kind, lambda: _restore(state))
            if result['ok']:
                return _verify_snapshot(state)
            return result
        def verify(args, kind=kind):
            return _verify_snapshot(_load_snapshot(args, kind))
        register(action_id, kind, validate, apply, verify, 'snapshot_id')
