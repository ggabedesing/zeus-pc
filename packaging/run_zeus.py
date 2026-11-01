"""Entrada do PyInstaller para o ZeusPC.exe.

O PyInstaller executa o script-alvo como módulo de topo (sem contexto de
pacote), então os imports relativos falhariam aqui: este arquivo só
delega para o launcher dentro do pacote.

    pyinstaller --onefile --windowed --name ZeusPC --paths src \
        --add-data "web;web" packaging/run_zeus.py
"""

import sys
from pathlib import Path

# Worker entry points must run before importing the server or opening its UI.
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    if '--maintenance-worker' in sys.argv:
        from zeus_pc.maintenance import worker_main
        return worker_main(sys.argv[1:])
    if '--desktop-worker' in sys.argv:
        from zeus_pc.desktop_runtime import worker_main
        return worker_main(sys.argv[1:])
    if '--layout-worker' in sys.argv:
        from zeus_pc.layout_runtime import worker_main
        return worker_main(sys.argv[1:])
    from zeus_pc.launcher import main as launch
    return launch()


if __name__ == '__main__':
    raise SystemExit(main())
