"""Model registry for the Zeus local brain (increment 018).

A ModelSpec is the closed, declarative contract for one local GGUF model:
identity, size/memory budget, license, languages and the tasks it is
allowed to serve. Nothing here downloads or executes anything; the
registry only knows what models MAY exist and under which rules.

Design mirrors the semantic ActionRegistry: fail-closed registration
(duplicate or malformed specs are rejected) and a deterministic default
lineup (sorted ids, no clock, no randomness).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

MODEL_CATALOG_VERSION = "1"
MODEL_ID_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)*$")
QUANT_RE = re.compile(r"^Q[0-9](?:_[A-Z0-9]+)*$")


class ModelTask(StrEnum):
    CLASSIFY = "CLASSIFY"
    SUMMARIZE = "SUMMARIZE"
    REASON = "REASON"
    PLAN = "PLAN"
    CHAT = "CHAT"


class BrainError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    title: str
    family: str
    filename: str
    quantization: str
    size_gb: float
    context_length: int
    min_ram_gb: float
    license_name: str
    languages: tuple[str, ...]
    strengths: tuple[ModelTask, ...]
    quality_rank: int
    source_url: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "title": self.title,
            "family": self.family,
            "filename": self.filename,
            "quantization": self.quantization,
            "size_gb": self.size_gb,
            "context_length": self.context_length,
            "min_ram_gb": self.min_ram_gb,
            "license_name": self.license_name,
            "languages": list(self.languages),
            "strengths": [task.value for task in self.strengths],
            "quality_rank": self.quality_rank,
            "source_url": self.source_url,
            "notes": self.notes,
        }


def _validate_spec(definition: ModelSpec) -> ModelSpec:
    model_id = str(definition.model_id or "").strip()
    if not MODEL_ID_RE.fullmatch(model_id):
        raise BrainError(f"model_id invalido: {definition.model_id!r}")
    if not str(definition.title or "").strip():
        raise BrainError(f"titulo obrigatorio para {model_id}")
    if not str(definition.family or "").strip():
        raise BrainError(f"familia obrigatoria para {model_id}")

    filename = str(definition.filename or "").strip()
    if not filename.endswith(".gguf") or "/" in filename or "\\" in filename:
        raise BrainError(
            f"filename deve ser um arquivo .gguf direto em models_dir: {filename!r}"
        )
    if not QUANT_RE.fullmatch(str(definition.quantization or "")):
        raise BrainError(f"quantizacao invalida em {model_id}")

    if definition.size_gb <= 0:
        raise BrainError(f"size_gb deve ser positivo em {model_id}")
    if definition.context_length <= 0:
        raise BrainError(f"context_length deve ser positivo em {model_id}")
    if definition.min_ram_gb <= 0:
        raise BrainError(f"min_ram_gb deve ser positivo em {model_id}")
    if definition.quality_rank <= 0:
        raise BrainError(f"quality_rank deve ser positivo em {model_id}")

    if not str(definition.license_name or "").strip():
        raise BrainError(f"licenca obrigatoria para {model_id}")
    if not definition.languages:
        raise BrainError(f"idiomas obrigatorios para {model_id}")
    if not definition.strengths:
        raise BrainError(f"tarefas obrigatorias para {model_id}")
    unknown = sorted(
        {task for task in definition.strengths if not isinstance(task, ModelTask)}
    )
    if unknown:
        raise BrainError(f"tarefas desconhecidas em {model_id}: {unknown}")

    return ModelSpec(
        model_id=model_id,
        title=str(definition.title).strip(),
        family=str(definition.family).strip(),
        filename=filename,
        quantization=str(definition.quantization).strip().upper(),
        size_gb=float(definition.size_gb),
        context_length=int(definition.context_length),
        min_ram_gb=float(definition.min_ram_gb),
        license_name=str(definition.license_name).strip(),
        languages=tuple(lang.strip().lower() for lang in definition.languages),
        strengths=tuple(dict.fromkeys(definition.strengths)),
        quality_rank=int(definition.quality_rank),
        source_url=str(definition.source_url or "").strip(),
        notes=str(definition.notes or "").strip(),
    )


class ModelRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ModelSpec] = {}

    def register(self, definition: ModelSpec) -> None:
        spec = _validate_spec(definition)
        if spec.model_id in self._items:
            raise BrainError(f"model_id duplicado: {spec.model_id}")
        self._items[spec.model_id] = spec

    def get(self, model_id: str) -> ModelSpec | None:
        return self._items.get(str(model_id or "").strip().upper())

    def ids(self) -> list[str]:
        return sorted(self._items)

    def for_task(self, task: ModelTask | str) -> list[ModelSpec]:
        wanted = ModelTask(str(task or "").strip().upper())
        candidates = [
            spec for spec in self._items.values() if wanted in spec.strengths
        ]
        return sorted(
            candidates,
            key=lambda spec: (spec.quality_rank, spec.model_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MODEL_CATALOG_VERSION,
            "count": len(self._items),
            "models": [
                self._items[model_id].to_dict() for model_id in self.ids()
            ],
        }


def build_default_model_registry() -> ModelRegistry:
    """The curated lineup: Japanese models + multilingual references.

    Licenses are declared per model; acceptance and download happen
    outside the registry (increment 019), never implicitly here.
    """
    registry = ModelRegistry()
    lineup: tuple[ModelSpec, ...] = (
        ModelSpec(
            model_id="ELYZA_JP_8B",
            title="ELYZA Japanese Llama 3 8B",
            family="ELYZA",
            filename="Llama-3-ELYZA-JP-8B.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=4.9,
            context_length=8192,
            min_ram_gb=8.0,
            license_name="Meta Llama 3 Community License",
            languages=("ja", "en"),
            strengths=(ModelTask.PLAN, ModelTask.REASON, ModelTask.CHAT),
            quality_rank=1,
            source_url="https://huggingface.co/elyza/Llama-3-ELYZA-JP-8B-GGUF",
            notes="Melhor raciocinio em japones; licenca da Meta exige aceite.",
        ),
        ModelSpec(
            model_id="QWEN25_7B",
            title="Qwen2.5 7B Instruct (multilingue)",
            family="Qwen",
            filename="Qwen2.5-7B-Instruct.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=4.4,
            context_length=32768,
            min_ram_gb=8.0,
            license_name="Apache-2.0",
            languages=("pt", "en", "ja", "es"),
            strengths=(
                ModelTask.CHAT,
                ModelTask.REASON,
                ModelTask.PLAN,
                ModelTask.SUMMARIZE,
            ),
            quality_rank=2,
            source_url="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF",
            notes="Referencia multilingue: unico do lineup com evidencia de portugues.",
        ),
        ModelSpec(
            model_id="SWALLOW_7B",
            title="NTT Swallow 7B Instruct",
            family="Swallow",
            filename="Swallow-7B-instruct.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=4.0,
            context_length=4096,
            min_ram_gb=7.0,
            license_name="Llama 2 Community License",
            languages=("ja", "en"),
            strengths=(ModelTask.REASON, ModelTask.PLAN, ModelTask.CHAT),
            quality_rank=3,
            source_url="https://swallow-llm.github.io/swallow-llama.en.html",
            notes="Continuacao do Llama 2 com japonês fortalecido.",
        ),
        ModelSpec(
            model_id="LLM_JP_1_8B",
            title="LLM-jp 3.1 1.8B Instruct",
            family="LLM-jp",
            filename="llm-jp-3.1-1.8b-instruct4.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=1.4,
            context_length=4096,
            min_ram_gb=3.0,
            license_name="Apache-2.0",
            languages=("ja", "en"),
            strengths=(
                ModelTask.CLASSIFY,
                ModelTask.SUMMARIZE,
                ModelTask.CHAT,
            ),
            quality_rank=4,
            source_url="https://huggingface.co/llm-jp/llm-jp-3.1-1.8b-instruct4",
            notes="O mais leve e juridicamente simples; ideal para tarefas curtas.",
        ),
        ModelSpec(
            model_id="STABLELM_JP_7B",
            title="Japanese StableLM Instruct Gamma 7B",
            family="StableLM",
            filename="japanese-stablelm-instruct-gamma-7b.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=4.2,
            context_length=4096,
            min_ram_gb=7.0,
            license_name="Apache-2.0",
            languages=("ja", "en"),
            strengths=(ModelTask.CHAT, ModelTask.SUMMARIZE),
            quality_rank=5,
            source_url=(
                "https://huggingface.co/stabilityai/"
                "japanese-stablelm-instruct-gamma-7b"
            ),
            notes="Licenca Apache simples; conversacao geral.",
        ),
        ModelSpec(
            model_id="RINNA_3_6B",
            title="rinna Japanese GPT-NeoX 3.6B Instruction PPO",
            family="rinna",
            filename="rinna-japanese-gpt-neox-3.6b-instruction-ppo.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=2.3,
            context_length=2048,
            min_ram_gb=4.0,
            license_name="MIT",
            languages=("ja",),
            strengths=(ModelTask.SUMMARIZE, ModelTask.CHAT),
            quality_rank=6,
            source_url=(
                "https://huggingface.co/rinna/"
                "japanese-gpt-neox-3.6b-instruction-ppo"
            ),
            notes="GGUF comunitario experimental; validar antes de usar no roteador.",
        ),
        ModelSpec(
            model_id="SARASHINA2_7B",
            title="SoftBank Sarashina2 7B",
            family="Sarashina",
            filename="sarashina2-7b.Q4_K_M.gguf",
            quantization="Q4_K_M",
            size_gb=4.0,
            context_length=4096,
            min_ram_gb=7.0,
            license_name="MIT",
            languages=("ja",),
            strengths=(ModelTask.REASON,),
            quality_rank=7,
            source_url="https://huggingface.co/sbintuitions/sarashina2-7b",
            notes="Base pre-treinada sem instruction tuning; reserva de raciocinio.",
        ),
    )
    for spec in lineup:
        registry.register(spec)
    return registry


DEFAULT_MODEL_REGISTRY = build_default_model_registry()
