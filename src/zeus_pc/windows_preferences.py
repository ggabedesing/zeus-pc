"""Explicit current-user preferences, with exact value/type/absence backups.

GPU preference uses Windows' persisted per-application setting. Registry readback
confirms the saved preference, not which GPU a running renderer actually uses.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from .app_paths import data_dir
from .powershell import run_json

EXPLORER = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
GPU_KEY = r'Software\Microsoft\DirectX\UserGpuPreferences'
_LOCK = threading.RLock()
_APP_CACHE = {'time': 0, 'items': []}


def _spec(id, title, description, category, path, name, values, restart=False):
    return {'id': id, 'title': title, 'description': description, 'category': category,
            'kind': 'toggle' if all(isinstance(k, bool) for k, _, _ in values) else 'select',
            'options': [{'value': k, 'label': label} for k, _, label in values],
            'restart': restart, 'requires_admin': False, '_path': path, '_name': name,
            '_values': values}


SPECS = {item['id']: item for item in [
    _spec('game_mode', 'Modo de Jogo', 'Ativar a preferência de Modo de Jogo do Windows. O efeito depende do jogo e da carga do PC.', 'Jogos',
          r'Software\Microsoft\GameBar', 'AutoGameModeEnabled', [(True, 1, 'Ativado'), (False, 0, 'Desativado')]),
    _spec('background_capture', 'Gravação de jogos em segundo plano', 'Controlar a captura histórica da Game Bar. Desativar preserva recursos quando você não usa replay.', 'Jogos',
          r'Software\Microsoft\Windows\CurrentVersion\GameDVR', 'HistoricalCaptureEnabled', [(True, 1, 'Ativada'), (False, 0, 'Desativada')]),
    _spec('file_extensions', 'Extensões dos arquivos', 'Mostrar .exe, .png e outras extensões no Explorador.', 'Explorer',
          EXPLORER, 'HideFileExt', [(True, 0, 'Mostrar'), (False, 1, 'Ocultar')], True),
    _spec('hidden_files', 'Arquivos ocultos', 'Controlar a exibição dos arquivos ocultos do usuário no Explorador.', 'Explorer',
          EXPLORER, 'Hidden', [(True, 1, 'Mostrar'), (False, 2, 'Ocultar')], True),
    _spec('compact_explorer', 'Explorador compacto', 'Diminuir o espaçamento entre arquivos e pastas.', 'Explorer',
          EXPLORER, 'UseCompactMode', [(True, 1, 'Compacto'), (False, 0, 'Confortável')], True),
    _spec('taskbar_alignment', 'Posição dos ícones da barra', 'Escolher alinhamento à esquerda ou centralizado no Windows 11.', 'Barra de tarefas',
          EXPLORER, 'TaskbarAl', [('left', 0, 'À esquerda'), ('center', 1, 'Centralizado')], True),
    _spec('taskbar_search', 'Busca na barra de tarefas', 'Escolher como a busca aparece. Algumas versões do Windows oferecem formatos adicionais.', 'Barra de tarefas',
          r'Software\Microsoft\Windows\CurrentVersion\Search', 'SearchboxTaskbarMode', [('hidden', 0, 'Oculta'), ('icon', 1, 'Ícone'), ('box', 2, 'Caixa')], True),
    _spec('menu_delay', 'Resposta dos menus', 'Ajustar o tempo antes de abrir submenus. Personaliza a resposta visual, sem acelerar o processador.', 'Interface',
          r'Control Panel\Desktop', 'MenuShowDelay', [('fast', '100', 'Rápida · 100 ms'), ('standard', '400', 'Padrão · 400 ms')], True),
]}


def catalog():
    return [{k: v for k, v in spec.items() if not k.startswith('_')} for spec in SPECS.values()]


def read_value(path, name):
    if os.name != 'nt':
        raise OSError('Disponível apenas no Windows.')
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, kind = winreg.QueryValueEx(key, name)
        if not isinstance(value, (str, int)):
            raise ValueError('Formato de preferência não suportado.')
        return {'exists': True, 'value': value, 'type': kind}
    except FileNotFoundError:
        return {'exists': False}


def write_value(path, name, state):
    import winreg
    if state['exists']:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, state['type'], state['value'])
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def notify():
    if os.name == 'nt':
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
            ctypes.c_ssize_t, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        response = ctypes.c_size_t()
        area = ctypes.create_unicode_buffer('Environment')
        user.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.cast(area, ctypes.c_void_p).value,
                                 2, 1000, ctypes.byref(response))


def preference_state(id):
    spec = SPECS[id]
    try:
        raw = read_value(spec['_path'], spec['_name'])
        selected = next((key for key, value, _ in spec['_values'] if raw.get('exists') and value == raw.get('value')), None)
        return {'available': True, 'value': selected, 'configured': raw['exists'], 'restart': spec['restart']}
    except (OSError, ValueError) as exc:
        return {'available': False, 'value': None, 'error': str(exc)}


def states():
    return {id: preference_state(id) for id in SPECS}


def _error(message):
    from .transaction import TransactionError
    return TransactionError(message)


def _validate(args):
    if not isinstance(args, dict) or set(args) != {'id', 'value'} or args['id'] not in SPECS:
        raise _error('Preferência não reconhecida.')
    if not any(type(args['value']) is type(key) and args['value'] == key for key, _, _ in SPECS[args['id']]['_values']):
        raise _error('Opção inválida para esta preferência.')
    return dict(args)


def _target(args):
    spec = SPECS[args['id']]
    raw = next(value for key, value, _ in spec['_values'] if type(key) is type(args['value']) and key == args['value'])
    return spec['_path'], spec['_name'], {'exists': True, 'type': 1 if isinstance(raw, str) else 4, 'value': raw}


def _backup(path, name):
    before = {'path': path, 'name': name, 'state': read_value(path, name), 'snapshot_id': secrets.token_hex(16)}
    folder = data_dir() / 'preference-snapshots'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (before['snapshot_id'] + '.json')).write_text(json.dumps(before), encoding='utf-8')
    return before


def _snapshot(args):
    if not isinstance(args, dict) or set(args) != {'snapshot_id'} or not re.fullmatch('[a-f0-9]{32}', str(args.get('snapshot_id', ''))):
        raise _error('Restauração inválida.')
    try:
        return json.loads((data_dir() / 'preference-snapshots' / (args['snapshot_id'] + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise _error('Registro de restauração não encontrado.')


def _capture(args):
    path, name, _ = _target(args)
    return _backup(path, name)


def _apply(args):
    path, name, state = _target(args)
    write_value(path, name, state)
    notify()
    return {'ok': True, 'message': 'Preferência salva no Windows.'}


def _verify(args):
    path, name, state = _target(args)
    return {'ok': read_value(path, name) == state, 'source': 'HKCU', 'restart': SPECS[args['id']]['restart']}


def _inverse(before):
    return {'action_id': 'RESTORE_WINDOWS_PREFERENCE', 'args': {'snapshot_id': before['snapshot_id']}}


def _restore(before):
    write_value(before['path'], before['name'], before['state'])
    notify()
    return {'ok': read_value(before['path'], before['name']) == before['state']}


def merge_gpu_preference(value, preference):
    if preference not in {'system', 'saving', 'high'}:
        raise _error('Preferência de GPU inválida.')
    replacement = {'saving': '1', 'high': '2'}.get(preference)
    fields, replaced = [], False
    for field in str(value or '').split(';'):
        if field.partition('=')[0].strip().lower() == 'gpupreference':
            if replacement is not None and not replaced:
                fields.append('GpuPreference=' + replacement)
            replaced = True
        elif field:
            fields.append(field)
    if replacement is not None and not replaced:
        fields.append('GpuPreference=' + replacement)
    return ';'.join(fields) + (';' if fields else '')


def _gpu_choice(value):
    for field in str(value or '').split(';'):
        key, _, val = field.partition('=')
        if key.strip().lower() == 'gpupreference':
            return {'1': 'saving', '2': 'high'}.get(val.strip(), 'system')
    return 'system'


def registered_applications():
    try:
        records = json.loads((data_dir() / 'gpu-applications.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    result = []
    for id, item in records.items():
        path = str(item.get('path') or '')
        if not item.get('pinned') or hashlib.sha256(path.lower().encode()).hexdigest()[:32] != id:
            continue
        if not Path(path).is_absolute() or Path(path).suffix.lower() != '.exe' or not Path(path).is_file():
            continue
        saved = read_value(GPU_KEY, path)
        result.append({**item, 'preference': _gpu_choice(saved.get('value'))})
    return result


def register_application(path):
    if not isinstance(path, str) or len(path) > 32768:
        raise ValueError('Indique o caminho completo de um aplicativo .exe.')
    target = Path(path.strip().strip('"'))
    if not target.is_absolute() or target.suffix.lower() != '.exe' or not target.is_file():
        raise ValueError('O arquivo .exe não foi encontrado neste computador.')
    target = target.resolve()
    item = {'id': hashlib.sha256(str(target).lower().encode()).hexdigest()[:32],
            'name': target.stem, 'path': str(target), 'pinned': True}
    with _LOCK:
        record_path = data_dir() / 'gpu-applications.json'
        try:
            records = json.loads(record_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            records = {}
        if len(records) >= 512 and item['id'] not in records:
            raise ValueError('O inventário atingiu o limite de aplicativos.')
        records[item['id']] = item
        record_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = record_path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        temporary.write_text(json.dumps(records), encoding='utf-8')
        temporary.replace(record_path)
    return item


def applications(refresh=False):
    with _LOCK:
        if not refresh and time.monotonic() - _APP_CACHE['time'] < 30:
            return list(_APP_CACHE['items'])
        if os.name != 'nt':
            return []
        items = run_json("Get-Process | Where-Object { $_.Path -and $_.MainWindowHandle -ne 0 } | Select-Object ProcessName,Path", timeout=12) or []
        if isinstance(items, dict):
            items = [items]
        seen, result = set(), []
        for item in items:
            path = str(item.get('Path') or '')
            if not path.lower().endswith('.exe') or path.lower() in seen or not Path(path).is_file():
                continue
            seen.add(path.lower())
            try:
                saved = read_value(GPU_KEY, path)
            except (OSError, ValueError):
                saved = {}
            result.append({'id': hashlib.sha256(path.lower().encode()).hexdigest()[:32], 'name': str(item.get('ProcessName') or Path(path).stem),
                           'path': path, 'preference': _gpu_choice(saved.get('value'))})
        _APP_CACHE.update(time=time.monotonic(), items=result[:64])
        return list(_APP_CACHE['items'])


def _gpu_validate(args):
    if not isinstance(args, dict) or set(args) != {'application_id', 'preference'} or args['preference'] not in {'system', 'saving', 'high'}:
        raise _error('Preferência de GPU inválida.')
    if not _known_application(args['application_id']):
        raise _error('Aplicativo fora do inventário atual. Atualize a lista.')
    return dict(args)


def _gpu_path(args):
    item = _known_application(args['application_id'])
    if not item:
        raise _error('Aplicativo não encontrado no inventário do Zeus.')
    return item['path']


def _known_application(application_id):
    with _LOCK:
        record_path = data_dir() / 'gpu-applications.json'
        try:
            records = json.loads(record_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            records = {}
        for app in applications():
            records[app['id']] = {'id': app['id'], 'name': app['name'], 'path': app['path'],
                                  'pinned': records.get(app['id'], {}).get('pinned', False)}
        item = records.get(application_id)
        if not item:
            return None
        path = item.get('path', '')
        expected_id = hashlib.sha256(path.lower().encode()).hexdigest()[:32]
        if expected_id != application_id or not Path(path).is_absolute() or not path.lower().endswith('.exe'):
            return None
        record_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = record_path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        temporary.write_text(json.dumps(records), encoding='utf-8')
        temporary.replace(record_path)
        return item


def _gpu_apply(args):
    path = _gpu_path(args)
    before = read_value(GPU_KEY, path)
    value = merge_gpu_preference(before.get('value', ''), args['preference'])
    write_value(GPU_KEY, path, {'exists': True, 'type': 1, 'value': value} if value else {'exists': False})
    return {'ok': True, 'message': 'Preferência gráfica salva. Feche e reabra o aplicativo para utilizá-la.'}


def _gpu_verify(args):
    saved = read_value(GPU_KEY, _gpu_path(args))
    return {'ok': _gpu_choice(saved.get('value')) == args['preference'], 'source': 'UserGpuPreferences', 'requires_application_restart': True}


def register_actions(registry):
    from .transaction import ActionDefinition
    common = dict(required_privilege='USER', reversible=True, resource_key='WINDOWS_PREFERENCES')
    registry.register(ActionDefinition(action_id='SET_WINDOWS_PREFERENCE', validate_args=_validate,
        capture_before=_capture, apply=_apply, verify=_verify, rollback_preview=_inverse,
        rollback=lambda before, _: _restore(before), **common))
    registry.register(ActionDefinition(action_id='RESTORE_WINDOWS_PREFERENCE',
        validate_args=lambda args: (_snapshot(args) and dict(args)),
        capture_before=lambda args: _backup(_snapshot(args)['path'], _snapshot(args)['name']),
        apply=lambda args: _restore(_snapshot(args)),
        verify=lambda args: {'ok': read_value(_snapshot(args)['path'], _snapshot(args)['name']) == _snapshot(args)['state']},
        rollback_preview=_inverse, rollback=lambda before, _: _restore(before), **common))
    registry.register(ActionDefinition(action_id='SET_GPU_PREFERENCE', validate_args=_gpu_validate,
        capture_before=lambda args: _backup(GPU_KEY, _gpu_path(args)), apply=_gpu_apply, verify=_gpu_verify,
        rollback_preview=_inverse, rollback=lambda before, _: _restore(before), **common))
