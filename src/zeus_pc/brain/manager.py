"""Managed model lifecycle for the Zeus brain (increment 019).

Turns "7 declared models" into "models you actually own and can trust":

- install: explicit license acceptance recorded in SQLite, then either an
  HTTPS download (allowlisted hosts only) or an import of a file the
  user already has. Bytes are streamed, hashed with SHA-256 and only
  then moved into models_dir — a hash mismatch leaves nothing behind;
- verify: recompute the hash of an installed GGUF and detect tampering;
- touch: every successful invoke marks the model as most recently used;
- storage: a deterministic LRU plan under a disk budget, demoting models
  that do not fit the available RAM first, applied only with an
  explicit confirmation and only for files this manager installed.

No machine mutations happen here; deleting a model file is data the
Zeus itself put there, never user files outside models_dir.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .registry import DEFAULT_MODEL_REGISTRY, BrainError, ModelRegistry, ModelSpec
from .runtime import available_ram_gb, model_path, models_dir

COPY_CHUNK_BYTES = 256 * 1024
DEFAULT_DISK_BUDGET_GB = float(os.environ.get("ZEUS_MODELS_BUDGET_GB", "30"))
DISK_SAFETY_MARGIN = 1.05
SHA256_HEX_RE_LEN = 64

# Beyond the host of each spec's source_url, these CDNs are the known
# mirrors of the curated repositories (HuggingFace resolve + GitHub).
EXTRA_DOWNLOAD_HOSTS = (
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.hf.co",
    "github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _url_host(url: str) -> str:
    cleaned = str(url or "").strip()
    if "://" not in cleaned:
        return ""
    return cleaned.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)[0].lower()


def sha256_file(path: Path) -> tuple[str, int]:
    """(hex digest, size in bytes) of a file, streamed in chunks."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _validate_expected_sha256(expected: str | None) -> str | None:
    if expected is None:
        return None
    cleaned = str(expected).strip().lower()
    if len(cleaned) != SHA256_HEX_RE_LEN or any(
        char not in "0123456789abcdef" for char in cleaned
    ):
        raise BrainError("expected_sha256 deve ser um hash SHA-256 hexadecimal.")
    return cleaned


def _validate_download_url(spec: ModelSpec, url: str) -> str:
    """Fail closed: HTTPS only, host on the curated allowlist."""
    cleaned = str(url or "").strip()
    if not cleaned.startswith("https://"):
        raise BrainError("Download negado: apenas HTTPS e permitido.")
    host = _url_host(cleaned)
    if not host:
        raise BrainError(f"URL de download invalida: {cleaned!r}")
    allowed = {item.lower() for item in EXTRA_DOWNLOAD_HOSTS}
    spec_host = _url_host(spec.source_url)
    if spec_host:
        allowed.add(spec_host)
    if host not in allowed:
        raise BrainError(
            f"Host de download negado: {host} (permitidos: {sorted(allowed)})."
        )
    return cleaned


class ModelManager:
    def __init__(
        self,
        database,
        *,
        registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
        directory: Path | None = None,
        open_url: Callable[[str], Any] | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.directory = Path(directory) if directory is not None else models_dir()
        # Transport injection for tests only; the HTTPS + host allowlist
        # guard runs before any I/O either way.
        self._open_url = open_url or self._default_open_url

    @staticmethod
    def _default_open_url(url: str):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ZeusPC/0.1"},
            method="GET",
        )
        return urllib.request.urlopen(request, timeout=60.0)

    # ------------------------------------------------------------------
    # install / verify
    # ------------------------------------------------------------------

    def install(
        self,
        model_id: str,
        *,
        accept_license: bool = False,
        url: str | None = None,
        source_path: str | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        spec = self.registry.get(model_id)
        if spec is None:
            raise BrainError(f"modelo nao registrado: {model_id!r}")
        if not accept_license:
            raise BrainError(
                f"licenca nao aceita: {spec.license_name} para {spec.model_id}; "
                "aceite explicito obrigatorio (accept_license)."
            )
        if bool(url) == bool(source_path):
            raise BrainError(
                "informe exatamente uma origem: url (download) ou source_path (import)."
            )

        self.directory.mkdir(parents=True, exist_ok=True)
        target = model_path(spec.filename, self.directory)
        if target.exists():
            raise BrainError(
                f"arquivo ja existe em {target}; use verify ou remova antes."
            )

        expected = _validate_expected_sha256(expected_sha256)
        free_bytes = shutil.disk_usage(self.directory).free

        if url:
            cleaned_url = _validate_download_url(spec, url)
            required = int(spec.size_gb * (1024**3) * DISK_SAFETY_MARGIN)
            if free_bytes < required:
                raise BrainError(
                    f"espaco insuficiente: {free_bytes / (1024**3):.1f} GB livres, "
                    f"~{spec.size_gb} GB necessarios."
                )
            sha256, size_bytes = self._download(cleaned_url, target, expected)
            source_url = cleaned_url
            source = "download"
        else:
            source_file = Path(str(source_path)).expanduser()
            if not source_file.is_file():
                raise BrainError(f"arquivo de origem inexistente: {source_file}")
            if free_bytes < source_file.stat().st_size:
                raise BrainError("espaco insuficiente para importar o arquivo.")
            sha256, size_bytes = self._import(source_file, target, expected)
            source_url = ""
            source = "import"

        accepted_at = _utc_now()
        self.database.record_model_install(
            model_id=spec.model_id,
            filename=spec.filename,
            license_name=spec.license_name,
            license_accepted_at=accepted_at,
            sha256=sha256,
            size_bytes=size_bytes,
            source=source,
            source_url=source_url,
        )
        return {
            "model_id": spec.model_id,
            "filename": spec.filename,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "size_gb": round(size_bytes / (1024**3), 2),
            "source": source,
            "source_url": source_url,
            "license_name": spec.license_name,
            "license_accepted_at": accepted_at,
            "path": str(target),
        }

    def _download(
        self,
        url: str,
        target: Path,
        expected_sha256: str | None,
    ) -> tuple[str, int]:
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with self._open_url(url) as response:
                with temporary.open("wb") as handle:
                    while True:
                        chunk = response.read(COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                        handle.write(chunk)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            temporary.unlink(missing_ok=True)
            raise BrainError(
                f"falha no download: {type(exc).__name__}"
            ) from exc

        sha256 = digest.hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            temporary.unlink(missing_ok=True)
            raise BrainError(
                "hash do download nao confere com o esperado; arquivo descartado."
            )
        os.replace(temporary, target)
        return sha256, size

    def _import(
        self,
        source_file: Path,
        target: Path,
        expected_sha256: str | None,
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with source_file.open("rb") as src, target.open("wb") as dst:
            while True:
                chunk = src.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                dst.write(chunk)

        sha256 = digest.hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            target.unlink(missing_ok=True)
            raise BrainError(
                "hash do arquivo importado nao confere com o esperado; "
                "copia descartada."
            )
        return sha256, size

    def verify(self, model_id: str) -> dict[str, Any]:
        spec = self.registry.get(model_id)
        if spec is None:
            raise BrainError(f"modelo nao registrado: {model_id!r}")
        install = self.database.get_model_install(spec.model_id)
        target = model_path(spec.filename, self.directory)

        if install is None:
            return {
                "model_id": spec.model_id,
                "ok": False,
                "status": "nao_rastreado",
                "detail": (
                    "arquivo sem hash registrado; reinstale pelo Zeus "
                    "para rastrear a origem."
                    if target.is_file()
                    else "nunca instalado pelo Zeus."
                ),
                "recorded_sha256": None,
                "current_sha256": None,
            }
        if not target.is_file():
            return {
                "model_id": spec.model_id,
                "ok": False,
                "status": "ausente",
                "detail": f"GGUF ausente: {target}",
                "recorded_sha256": install["sha256"],
                "current_sha256": None,
            }

        current, size_bytes = sha256_file(target)
        ok = current == install["sha256"]
        return {
            "model_id": spec.model_id,
            "ok": ok,
            "status": "ok" if ok else "alterado",
            "detail": (
                "hash confere."
                if ok
                else "arquivo alterado desde a instalacao; reinstale antes de usar."
            ),
            "recorded_sha256": install["sha256"],
            "current_sha256": current,
            "size_bytes": size_bytes,
        }

    # ------------------------------------------------------------------
    # usage tracking and eviction
    # ------------------------------------------------------------------

    def touch(self, model_id: str) -> bool:
        return self.database.touch_model_used(model_id)

    def remove(self, model_id: str, *, confirm: bool = False) -> dict[str, Any]:
        spec = self.registry.get(model_id)
        if spec is None:
            raise BrainError(f"modelo nao registrado: {model_id!r}")
        install = self.database.get_model_install(spec.model_id)
        if install is None:
            raise BrainError(
                f"{spec.model_id} nao foi instalado pelo Zeus; nada a remover."
            )
        if not confirm:
            raise BrainError("remocao exige confirmacao explicita (confirm).")

        target = model_path(spec.filename, self.directory)
        freed = 0
        if target.is_file():
            freed = target.stat().st_size
            target.unlink()
        self.database.delete_model_install(spec.model_id)
        return {
            "model_id": spec.model_id,
            "removed": True,
            "freed_bytes": freed,
            "freed_gb": round(freed / (1024**3), 2),
        }

    def _entries(self, available_gb: float | None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for install in self.database.list_model_installs():
            spec = self.registry.get(install["model_id"])
            if spec is None:  # pragma: no cover - registry/database invariant
                continue
            target = model_path(spec.filename, self.directory)
            entries.append(
                {
                    "model_id": spec.model_id,
                    "filename": spec.filename,
                    "size_bytes": int(install["size_bytes"]),
                    "size_gb": round(install["size_bytes"] / (1024**3), 2),
                    "sha256": install["sha256"],
                    "source": install["source"],
                    "installed_at": install["installed_at"],
                    "last_used_at": install["last_used_at"] or "",
                    "present": target.is_file(),
                    "fits_ram": (
                        available_gb is None
                        or spec.min_ram_gb <= available_gb
                    ),
                }
            )
        return entries

    def storage_status(
        self,
        *,
        budget_gb: float | None = None,
        available_ram: float | None = None,
    ) -> dict[str, Any]:
        budget = (
            float(budget_gb)
            if budget_gb is not None
            else DEFAULT_DISK_BUDGET_GB
        )
        if budget <= 0:
            raise BrainError("budget_gb deve ser positivo.")
        available = (
            available_ram if available_ram is not None else available_ram_gb()
        )
        entries = self._entries(available)
        keep, evict, used_bytes = self._plan(entries, budget)
        return {
            "budget_gb": round(budget, 2),
            "used_gb": round(used_bytes / (1024**3), 2),
            "tracked_count": len(entries),
            "available_ram_gb": available,
            "installs": entries,
            "plan": {
                "fits": not evict,
                "keep": keep,
                "evict": evict,
            },
            "scores": self.database.benchmark_scores(),
        }

    @staticmethod
    def _plan(
        entries: list[dict[str, Any]],
        budget_gb: float,
    ) -> tuple[list[str], list[dict[str, Any]], int]:
        """Keep most-recently-used first; demote RAM-heavy models.

        Order for keeping: models that fit RAM first, then most recently
        used (never used = oldest), model_id as the deterministic tie
        break. Everything past the budget is an eviction candidate.
        """
        budget_bytes = int(budget_gb * (1024**3))
        present = [entry for entry in entries if entry["present"]]
        # Stable sorts, most specific last: model_id tie-break, then
        # recency (newest first, never-used last), then RAM fit first.
        ordered = sorted(present, key=lambda entry: entry["model_id"])
        ordered = sorted(
            ordered,
            key=lambda entry: entry["last_used_at"],
            reverse=True,
        )
        ordered = sorted(
            ordered,
            key=lambda entry: 0 if entry["fits_ram"] else 1,
        )
        keep: list[str] = []
        evict: list[dict[str, Any]] = []
        used = 0
        for entry in ordered:
            if used + entry["size_bytes"] <= budget_bytes:
                keep.append(entry["model_id"])
                used += entry["size_bytes"]
            else:
                evict.append(
                    {
                        "model_id": entry["model_id"],
                        "size_gb": entry["size_gb"],
                        "reason": (
                            "memoria"
                            if not entry["fits_ram"]
                            else "lru"
                        ),
                    }
                )
        return sorted(keep), evict, used

    def prune(
        self,
        *,
        budget_gb: float | None = None,
        confirm: bool = False,
        available_ram: float | None = None,
    ) -> dict[str, Any]:
        if not confirm:
            raise BrainError(
                "descarte exige confirmacao explicita (confirm); "
                "use storage_status para ver o plano antes."
            )
        status = self.storage_status(
            budget_gb=budget_gb,
            available_ram=available_ram,
        )
        removed: list[dict[str, Any]] = []
        for item in status["plan"]["evict"]:
            spec = self.registry.get(item["model_id"])
            if spec is None:  # pragma: no cover - registry invariant
                continue
            target = model_path(spec.filename, self.directory)
            freed = target.stat().st_size if target.is_file() else 0
            target.unlink(missing_ok=True)
            self.database.delete_model_install(spec.model_id)
            removed.append({**item, "freed_bytes": freed})
        freed_bytes = sum(item["freed_bytes"] for item in removed)
        return {
            "removed": removed,
            "kept": status["plan"]["keep"],
            "freed_gb": round(freed_bytes / (1024**3), 2),
            "budget_gb": status["budget_gb"],
        }
