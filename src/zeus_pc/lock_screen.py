"""Windows lock-screen IMAGE adapter through the OS WinRT API.

Image-only restoration does not restore Spotlight/slideshow provider selection.
The credential/password form remains owned by Windows.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time

from .app_paths import data_dir
from .personalization import import_wallpaper, wallpaper_asset, MAX_IMAGE_BYTES
from .powershell import run_json

_LOCK = threading.RLock()
_CAPABILITY = {'time': 0, 'value': None}
_BRIDGE = r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.System.UserProfile.UserProfilePersonalizationSettings,Windows.System.UserProfile,ContentType=WindowsRuntime]
$null=[Windows.System.UserProfile.LockScreen,Windows.System.UserProfile,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
function Await($Operation,$Type) {
  $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Operation))
  return $task.GetAwaiter().GetResult()
}
function ReadImage {
  $random=[Windows.System.UserProfile.LockScreen]::GetImageStream()
  try {
    if ($random.Size -gt 20971520) { throw 'A imagem atual excede 20 MiB.' }
    $stream=[System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($random)
    try {
      $memory=New-Object System.IO.MemoryStream
      try { $stream.CopyTo($memory); return [Convert]::ToBase64String($memory.ToArray()) }
      finally { $memory.Dispose() }
    } finally { $stream.Dispose() }
  } finally { $random.Dispose() }
}
if (-not [Windows.System.UserProfile.UserProfilePersonalizationSettings]::IsSupported()) {
  throw 'A API de tela de bloqueio não está disponível neste Windows.'
}
'''


class NativeLockScreen:
    supported = os.name == 'nt'

    def read(self):
        if not self.supported:
            raise OSError('Disponível apenas no Windows.')
        result = run_json(_BRIDGE + "\n[PSCustomObject]@{image=(ReadImage)}", timeout=25)
        image = base64.b64decode(result['image'], validate=True)
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise ValueError('A imagem atual não pôde ser capturada.')
        return image

    def set(self, path):
        payload = base64.b64encode(json.dumps({'path': str(path)}, ensure_ascii=False).encode()).decode('ascii')
        script = _BRIDGE + f"\n$data=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')))\n" + r'''
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($data.path)) ([Windows.Storage.StorageFile])
$settings=[Windows.System.UserProfile.UserProfilePersonalizationSettings]::Current
$ok=Await ($settings.TrySetLockScreenImageAsync($file)) ([bool])
if (-not $ok) { throw 'O Windows recusou a alteração da tela de bloqueio.' }
[PSCustomObject]@{image=(ReadImage)}
'''
        result = run_json(script, timeout=35)
        saved = base64.b64decode(result['image'], validate=True)
        return hashlib.sha256(saved).hexdigest()


_adapter = NativeLockScreen()


def current_state():
    if time.monotonic() - _CAPABILITY['time'] < 30 and _CAPABILITY['value'] is not None:
        return dict(_CAPABILITY['value'])
    state = {'supported': _adapter.supported, 'available': False,
             'scope': 'image', 'message': 'Altera a imagem. A restauração não recompõe Spotlight ou apresentação de slides.'}
    try:
        if _adapter.supported:
            # No image bytes leave this local process, except a managed capture on APPLY.
            _adapter.read()
            state['available'] = True
    except Exception as exc:
        state['message'] = str(exc)
    _CAPABILITY.update(time=time.monotonic(), value=state)
    return dict(state)


def _error(message):
    from .transaction import TransactionError
    return TransactionError(message)


def _folder():
    path = data_dir() / 'lock-screen'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate(args):
    if not isinstance(args, dict) or set(args) != {'asset_id'} or not isinstance(args['asset_id'], str):
        raise _error('Selecione uma imagem importada pelo Zeus.')
    try:
        wallpaper_asset(args['asset_id'])
    except (OSError, ValueError, KeyError):
        raise _error('A imagem não existe na biblioteca do Zeus.')
    return dict(args)


def _capture(_):
    image = _adapter.read()
    asset = import_wallpaper(image)
    before = {'snapshot_id': secrets.token_hex(16), 'asset_id': asset['id'],
              'digest': hashlib.sha256(image).hexdigest(), 'scope': 'image-only'}
    (_folder() / (before['snapshot_id'] + '.json')).write_text(json.dumps(before), encoding='utf-8')
    return before


def _snapshot(args):
    if not isinstance(args, dict) or set(args) != {'snapshot_id'} or not re.fullmatch('[0-9a-f]{32}', str(args['snapshot_id'])):
        raise _error('Restauração de imagem inválida.')
    try:
        return json.loads((_folder() / (args['snapshot_id'] + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise _error('Imagem anterior não encontrada.')


def _set_asset(asset_id):
    asset = wallpaper_asset(asset_id)
    # WinRT may cache a source URI: use a fresh managed filename for every write.
    source = _folder() / (secrets.token_hex(16) + asset.suffix)
    shutil.copyfile(asset, source)
    return _adapter.set(source)


def _apply(args):
    with _LOCK:
        before = import_wallpaper(_adapter.read())
        try:
            digest = _set_asset(args['asset_id'])
            (_folder() / ('receipt-' + args['asset_id'] + '.json')).write_text(json.dumps({'digest': digest}), encoding='utf-8')
        except Exception:
            _set_asset(before['id'])
            raise
        return {'ok': True, 'message': 'Imagem da tela de bloqueio alterada pelo Windows. Restauração disponível para a imagem estática.'}


def _verify(args):
    try:
        expected = json.loads((_folder() / ('receipt-' + args['asset_id'] + '.json')).read_text(encoding='utf-8'))['digest']
        actual = hashlib.sha256(_adapter.read()).hexdigest()
        return {'ok': actual == expected, 'source': 'LockScreen.GetImageStream', 'digest': actual, 'scope': 'image-only'}
    except (OSError, ValueError, KeyError):
        return {'ok': False, 'source': 'LockScreen.GetImageStream'}


def _inverse(before):
    return {'action_id': 'RESTORE_LOCK_SCREEN_IMAGE', 'args': {'snapshot_id': before['snapshot_id']}}


def _restore(before):
    expected = _set_asset(before['asset_id'])
    (_folder() / ('restored-' + before['snapshot_id'] + '.json')).write_text(json.dumps({'digest': expected}), encoding='utf-8')
    return {'ok': hashlib.sha256(_adapter.read()).hexdigest() == expected, 'scope': 'image-only'}


def _verify_restore(args):
    before = _snapshot(args)
    receipt = json.loads((_folder() / ('restored-' + before['snapshot_id'] + '.json')).read_text(encoding='utf-8'))
    return {'ok': hashlib.sha256(_adapter.read()).hexdigest() == receipt['digest'], 'scope': 'image-only'}


def register_actions(registry):
    from .transaction import ActionDefinition
    common = dict(resource_key='WINDOWS_LOCK_SCREEN_IMAGE', required_privilege='USER', reversible=True)
    registry.register(ActionDefinition(action_id='SET_LOCK_SCREEN_WALLPAPER', validate_args=_validate,
        capture_before=_capture, apply=_apply, verify=_verify, rollback_preview=_inverse,
        rollback=lambda before, _: _restore(before), **common))
    registry.register(ActionDefinition(action_id='RESTORE_LOCK_SCREEN_IMAGE',
        validate_args=lambda args: (_snapshot(args) and dict(args)), capture_before=_capture,
        apply=lambda args: _restore(_snapshot(args)),
        verify=_verify_restore,
        rollback_preview=_inverse, rollback=lambda before, _: _restore(before), **common))
