"""Caminhos de aplicativo conscientes de "frozen" (Increment 023 — packaging).

Em desenvolvimento a raiz é o repositório (src/zeus_pc/... sobe 2 níveis).
Dentro de um EXE o PyInstaller extrai o bundle para uma pasta temporária
(sys._MEIPASS) que some ao fechar: lá ficam só os assets somente-leitura
(web/). O dado persistente (data/zeus.db) precisa viver num diretório
estável — %LOCALAPPDATA%\\ZeusPC\\data no Windows.

Override universal: ZEUS_DATA_DIR força o diretório de dados em qualquer
modo (testes, instalação portátil, diagnóstico remoto).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True quando rodando dentro de um executável gerado pelo PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Pasta de leitura do código/assets: raiz do bundle quando congelado,
    senão a raiz do repositório."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # Modo onedir (sem extração): os assets ficam ao lado do EXE.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Pasta de dados que persiste entre execuções (SQLite, logs, modelos)."""
    override = os.environ.get("ZEUS_DATA_DIR")
    if override:
        return Path(override)
    if is_frozen():
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return Path(base) / "ZeusPC" / "data"
        return Path.home() / ".local" / "share" / "zeus-pc" / "data"
    return bundle_dir() / "data"


def web_dir() -> Path:
    """Pasta da interface (index.html, app.js, styles.css). No EXE vem do bundle."""
    return bundle_dir() / "web"
