"""Delete only old, unchanged regular files from the user's temporary folder."""
import os
import stat
import tempfile
import time
from pathlib import Path


def temp_root():
    return Path(tempfile.gettempdir()).resolve()


def _reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def preview_temp(root, *, max_files=15000):
    root = Path(root).resolve()
    cutoff = time.time() - 7 * 86400
    files = []
    scanned = 0
    started = time.monotonic()
    truncated = False
    for directory, folders, names in os.walk(root, followlinks=False):
        safe_dirs = []
        for name in folders:
            try:
                path = Path(directory) / name
                info = path.lstat()
                if not path.is_symlink() and not _reparse(info):
                    safe_dirs.append(name)
            except OSError:
                pass
        folders[:] = safe_dirs
        for name in names:
            scanned += 1
            if scanned > max_files or time.monotonic() - started > 4:
                truncated = True
                break
            path = Path(directory) / name
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or _reparse(info) or info.st_mtime >= cutoff:
                    continue
                if root not in path.resolve().parents:
                    continue
                files.append({'path': str(path), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                              'ino': info.st_ino, 'dev': info.st_dev})
            except OSError:
                pass
        if truncated:
            break
    return {'bytes': sum(f['size'] for f in files), 'files': files, 'truncated': truncated}


def clean_temp(root, files):
    root = Path(root).resolve()
    result = {'freed_bytes': 0, 'deleted': 0, 'skipped': 0}
    for entry in files:
        try:
            path = Path(entry['path'])
            info = path.lstat()
            # Resolve every ancestor again: a junction/symlink may have appeared since preview.
            ancestors = path.relative_to(root).parts[:-1]
            parent = root
            for part in ancestors:
                parent = parent / part
                if parent.is_symlink() or _reparse(parent.lstat()):
                    raise ValueError('Link in temporary path')
            if root not in path.resolve().parents or not stat.S_ISREG(info.st_mode) or _reparse(info):
                raise ValueError('Not a regular temporary file')
            identity = (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
            if identity != (entry['size'], entry['mtime_ns'], entry['ino'], entry['dev']):
                raise ValueError('Temporary file changed')
            path.unlink()
            result['freed_bytes'] += info.st_size
            result['deleted'] += 1
        except (OSError, ValueError, KeyError):
            result['skipped'] += 1
    result.update(ok=True, message=f"{result['deleted']} arquivos temporários removidos. {result['skipped']} preservados.", state='VERIFIED')
    return result
