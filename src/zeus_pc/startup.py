"""Toggle only existing per-user startup entries, preserving their exact data."""
import os

RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
BACKUP = r'Software\ZeusPC\StartupBackup'
APPROVAL = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'


def _approval_allows(name):
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVAL) as key:
            value, kind = winreg.QueryValueEx(key, name)
        # Respect Windows/Task Manager decisions. Unknown approval formats are
        # outside this app's startup actions; never rewrite the approval record.
        return kind == winreg.REG_BINARY and len(value) >= 4 and int.from_bytes(value[:4], 'little') == 2
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _values(path):
    if os.name != 'nt':
        return {}
    import winreg
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, i)
                if kind in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} and name:
                    result[name] = (value, kind)
    except FileNotFoundError:
        pass
    return result


def list_startup():
    active, disabled = _values(RUN), _values(BACKUP)
    return [{'name': name, 'command': value[0], 'enabled': enabled}
            for enabled, entries in ((True, active), (False, disabled))
            for name, value in sorted(entries.items(), key=lambda p: p[0].lower())
            if not (not enabled and name in active) and (not enabled or _approval_allows(name))]


def _put(path, name, value):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, value[1], value[0])


def _delete(path, name):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, name)


def set_startup_enabled(name, enabled):
    if not _approval_allows(name):
        raise ValueError('Esta entrada é controlada pelo Windows. Revise em Aplicativos de inicialização.')
    source, target = (BACKUP, RUN) if enabled else (RUN, BACKUP)
    source_values, target_values = _values(source), _values(target)
    if name not in source_values:
        raise ValueError('A entrada de inicialização mudou. Analise novamente.')
    if name in target_values:
        raise ValueError('Já existe uma entrada com esse nome. Nenhum valor foi substituído.')
    value = source_values[name]
    _put(target, name, value)
    try:
        _delete(source, name)
    except OSError:
        _delete(target, name)
        raise
