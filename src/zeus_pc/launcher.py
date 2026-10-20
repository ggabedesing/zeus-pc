"""Launcher do Zeus PC empacotado como EXE (Increment 023 — packaging).

Fluxo do main():

1. Garante o diretório de dados (LOCALAPPDATA no EXE) e manda stdout/stderr
   para data/zeus-launcher.log quando não há console (janela PyInstaller).
2. Porta ocupada por outro Zeus -> só abre o navegador (instância única).
   Porta ocupada por outro programa -> diálogo de erro e sai com 1.
3. Caso contrário: cria o servidor (create_server), serve em um thread,
   espera /api/status responder, abre o navegador e mostra a janela de
   status (Tk): "Abrir painel" e "Parar e sair". Fechar a janela encerra
   o servidor; sem Tk, o servidor segue sem janela (Ctrl+C encerra).

Qualquer exceção vira traceback em data/zeus-launcher.log (+ diálogo no
Windows quando congelado).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from . import server
from .app_paths import data_dir, is_frozen

LAUNCHER_LOG = "zeus-launcher.log"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def server_url(port: int) -> str:
    return f"http://{server.HOST}:{port}"


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def is_zeus_running(url: str, timeout: float = 0.6) -> bool:
    """True quando já há um Zeus PC respondendo em `url` (instância única)."""
    try:
        with _OPENER.open(f"{url}/api/status", timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return isinstance(payload, dict) and "version" in payload
    except Exception:
        return False


def wait_until_serving(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_zeus_running(url, timeout=min(1.0, timeout)):
            return True
        time.sleep(0.25)
    return False


def _log(line: str) -> None:
    try:
        target = data_dir()
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(target / LAUNCHER_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _redirect_stdio(log_path: Path) -> None:
    """EXE windowed não tem console: manda prints perdidos para o log."""
    try:
        if sys.stdout is None or sys.stderr is None:
            handle = open(log_path, "a", encoding="utf-8", errors="replace")
            if sys.stdout is None:
                sys.stdout = handle
            if sys.stderr is None:
                sys.stderr = handle
    except OSError:
        pass


def _fatal(message: str) -> None:
    _log(f"ERRO: {message}")
    if os.name == "nt" and is_frozen():
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Zeus PC", 0x10)
            return
        except Exception:
            pass
    print(f"[ZEUS][ERRO] {message}", file=sys.stderr)


def _status_window(httpd, url: str) -> None:
    """Janela de controle do EXE. Fechar (ou 'Parar e sair') derruba o servidor."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        _log(f"sem tkinter ({exc}); servidor segue sem janela de controle")
        threading.Event().wait()
        return

    root = tk.Tk()
    root.title("Zeus PC")
    root.resizable(False, False)
    body = ttk.Frame(root, padding=16)
    body.grid(row=0, column=0)
    ttk.Label(body, text="Zeus PC está no ar", font=("Segoe UI", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(body, text=url, foreground="#555555").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(2, 10)
    )
    ttk.Button(body, text="Abrir painel", command=lambda: webbrowser.open(url)).grid(
        row=2, column=0, sticky="we", padx=(0, 6)
    )
    ttk.Button(body, text="Parar e sair", command=root.destroy).grid(
        row=2, column=1, sticky="we"
    )
    ttk.Label(
        body, text="Feche a janela para encerrar o servidor.", foreground="#777777"
    ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
    body.columnconfigure((0, 1), weight=1)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    x = max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)
    y = max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 3)
    root.geometry(f"+{x}+{y}")
    root.mainloop()


def main() -> int:
    httpd = None
    serving = False
    try:
        data = data_dir()
        data.mkdir(parents=True, exist_ok=True)
        _redirect_stdio(data / LAUNCHER_LOG)

        port = server.PORT
        url = server_url(port)
        if port_in_use(server.HOST, port):
            if is_zeus_running(url):
                _log(f"ja em execucao em {url}; abrindo o navegador")
                webbrowser.open(url)
                return 0
            _fatal(
                f"A porta {port} esta em uso por outro programa.\n"
                "Feche o programa ou defina ZEUS_PORT com outra porta."
            )
            return 1

        httpd = server.create_server()
        threading.Thread(
            target=httpd.serve_forever, daemon=True, name="zeus-http"
        ).start()
        serving = True
        if not wait_until_serving(url):
            raise RuntimeError(f"o servidor nao respondeu em {url}")
        _log(f"servindo em {url}")
        webbrowser.open(url)
        _status_window(httpd, url)
        return 0
    except KeyboardInterrupt:
        _log("interrompido pelo usuario")
        return 0
    except Exception:
        _log(traceback.format_exc())
        _fatal(
            "O Zeus PC falhou ao iniciar. Detalhes em:\n"
            + str(data_dir() / LAUNCHER_LOG)
        )
        return 1
    finally:
        if httpd is not None:
            try:
                if serving:
                    httpd.shutdown()
            finally:
                server.finalize_server(httpd)
            _log("servidor encerrado")


if __name__ == "__main__":
    raise SystemExit(main())
