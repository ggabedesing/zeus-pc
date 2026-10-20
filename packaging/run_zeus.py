"""Entrada do PyInstaller para o ZeusPC.exe.

O PyInstaller executa o script-alvo como módulo de topo (sem contexto de
pacote), então os imports relativos falhariam aqui: este arquivo só
delega para o launcher dentro do pacote.

    pyinstaller --onefile --windowed --name ZeusPC --paths src \
        --add-data "web;web" packaging/run_zeus.py
"""

from zeus_pc.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
