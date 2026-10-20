"""Gera packaging/zeus.ico com o Z do brand — só stdlib.

Quadrado arredondado com gradiente #66e08a -> #3fae68 (mesmas cores do
web/styles.css) e o "Z" branco. As entradas do ICO são DIB/BMP32 (formato
clássico aceito pelo PyInstaller e por qualquer Windows), renderizadas com
supersampling e mix alpha para suavizar as bordas.

Uso: python packaging/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

TOP = (0x66, 0xE0, 0x8A)
BOTTOM = (0x3F, 0xAE, 0x68)
SIZES = (16, 32, 48, 256)
OUT = Path(__file__).resolve().parent / "zeus.ico"


def _inside(x: int, y: int, size: int, radius: int) -> bool:
    """True dentro do quadrado arredondado."""
    if not (0 <= x < size and 0 <= y < size):
        return False
    if radius <= x <= size - 1 - radius or radius <= y <= size - 1 - radius:
        return True
    cx = radius if x < radius else size - 1 - radius
    cy = radius if y < radius else size - 1 - radius
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _is_z(x: int, y: int, size: int) -> bool:
    """Máscara do 'Z': barra superior, diagonal e barra inferior."""
    margin = max(1, size // 5)
    stroke = max(1, size // 7)
    x0, x1 = margin, size - 1 - margin
    if x0 <= x <= x1 and margin <= y <= margin + stroke:
        return True
    if x0 <= x <= x1 and size - 1 - margin - stroke <= y <= size - 1 - margin:
        return True
    top, bottom = margin + stroke, size - 1 - margin - stroke
    if top <= y <= bottom and bottom > top:
        progress = (y - top) / (bottom - top)
        center = x1 - (x1 - x0) * progress
        half = stroke / 2
        if center - half <= x <= center + half:
            return True
    return False


def _render(size: int, ss: int) -> bytes:
    """RGBA em (size*ss)^2, antes da redução."""
    big = size * ss
    radius = max(2, big // 6)
    buf = bytearray(big * big * 4)
    for y in range(big):
        t = y / (big - 1)
        br = round(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        bg = round(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        bb = round(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        row = y * big
        for x in range(big):
            if not _inside(x, y, big, radius):
                continue
            i = (row + x) * 4
            if _is_z(x, y, big):
                buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = 255, 255, 255, 255
            else:
                buf[i], buf[i + 1], buf[i + 2], buf[i + 3] = br, bg, bb, 255
    return bytes(buf)


def _downsample(buf: bytes, size: int, ss: int) -> bytes:
    """Box filter com acumulador pré-multiplicado (sem halo escuro na borda)."""
    big = size * ss
    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            acc_a = acc_r = acc_g = acc_b = 0
            for dy in range(ss):
                base = ((y * ss + dy) * big + x * ss) * 4
                for dx in range(ss):
                    i = base + dx * 4
                    a = buf[i + 3]
                    acc_a += a
                    acc_r += buf[i] * a
                    acc_g += buf[i + 1] * a
                    acc_b += buf[i + 2] * a
            n = ss * ss
            o = (y * size + x) * 4
            if acc_a:
                out[o] = acc_r // acc_a
                out[o + 1] = acc_g // acc_a
                out[o + 2] = acc_b // acc_a
            out[o + 3] = acc_a // n
    return bytes(out)


def _dib(rgba: bytes, size: int) -> bytes:
    """DIB 32bpp: cabeçalho + pixels BGRA bottom-up + máscara AND zerada."""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        2 * size,
        1,
        32,
        0,
        size * size * 4,
        0,
        0,
        0,
        0,
    )
    rows = bytearray()
    for y in range(size - 1, -1, -1):  # bottom-up
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for i in range(0, len(row), 4):
            rows += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytes(mask_stride * size)  # alpha do pixel já trata as bordas
    return header + bytes(rows) + mask


def _ico(frames: dict[int, bytes]) -> bytes:
    entries = sorted(frames.items())
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    blobs = b""
    for size, dib in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(dib), offset
        )
        blobs += dib
        offset += len(dib)
    return header + directory + blobs


def main() -> None:
    frames: dict[int, bytes] = {}
    for size in SIZES:
        ss = 4 if size <= 48 else 2
        rgba = _downsample(_render(size, ss), size, ss)
        frames[size] = _dib(rgba, size)
    blob = _ico(frames)
    # Sanidade: cabeçalho ICO + 4 entradas DIB de tamanho coerente.
    assert blob[:6] == b"\x00\x00\x01\x00\x04\x00", "cabecalho ICO invalido"
    OUT.write_bytes(blob)
    print(f"{OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
