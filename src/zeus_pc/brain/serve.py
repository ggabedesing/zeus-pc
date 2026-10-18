"""CLI helper: start a loopback llama-server for one registered model.

    python -m zeus_pc.brain.serve --model-id LLM_JP_1_8B

The subprocess is spawned with an argv list (never a shell string),
bound to 127.0.0.1 only, and exits with the server's own code.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .registry import DEFAULT_MODEL_REGISTRY, BrainError
from .runtime import detect_llama_server, model_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zeus_pc.brain.serve",
        description="Sobe o llama-server local para um modelo registrado.",
    )
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--n-gpu-layers",
        type=int,
        default=None,
        help="Camadas na GPU (omitir = CPU apenas).",
    )
    args = parser.parse_args(argv)

    try:
        spec = DEFAULT_MODEL_REGISTRY.get(args.model_id)
        if spec is None:
            raise BrainError(f"modelo nao registrado: {args.model_id!r}")
        binary = detect_llama_server()
        if not binary:
            raise BrainError(
                "llama-server nao encontrado: instale o llama.cpp ou "
                "aponte ZEUS_LLAMA_SERVER para o executavel."
            )
        gguf = model_path(spec.filename)
        if not gguf.is_file():
            raise BrainError(f"GGUF ausente: {gguf}")
        port = min(max(int(args.port), 1024), 65535)
    except BrainError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    command = [
        binary,
        "-m",
        str(gguf),
        "-c",
        str(spec.context_length),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if args.n_gpu_layers is not None:
        command.extend(["-ngl", str(int(args.n_gpu_layers))])

    print(f"Zeus brain: {spec.model_id} em http://127.0.0.1:{port}")
    try:
        CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            command,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return int(completed.returncode)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
