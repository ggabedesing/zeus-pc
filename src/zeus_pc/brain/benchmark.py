"""Closed JA/EN/PT benchmark for installed models (increment 019).

Measures what a local model actually delivers before the router trusts
it: a fixed prompt set per language (never generated at runtime), each
turn scored by language fidelity (does the answer really come back in
the requested language?) plus throughput against a documented CPU
reference. Every turn is journaled in SQLite; the per-model aggregate is
what the router uses as a tie-break above the static quality_rank.

The suite is deterministic by construction: same prompts, same weights,
same order. Only the machine measurements vary.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .registry import DEFAULT_MODEL_REGISTRY, BrainError, ModelRegistry, ModelTask
from .runtime import DEFAULT_BASE_URL, chat_complete, model_path, models_dir, probe_server

LANGUAGE_SET = ("ja", "en", "pt")

# Scoring weights: fidelity dominates, speed saturates at the reference.
# 20 tokens/s is a realistic llama-server CPU figure for the curated
# 7-8B Q4 lineup; above it, extra throughput adds nothing.
SPEED_REFERENCE_TOKENS_PER_S = 20.0
FIDELITY_SHARE = 60.0
SPEED_SHARE = 40.0
BENCHMARK_MAX_TOKENS = 128

_JA_SCRIPT = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_EN_WORDS = re.compile(
    r"\b(the|is|are|and|to|of|in|that|it|for|with|this|can|you|what|how)\b",
    re.IGNORECASE,
)
_PT_WORDS = re.compile(
    r"\b(de|que|para|com|uma|voce|você|nao|não|mais|este|esta|isso|responda|"
    r"resuma|classifique|computador|lento|lenta)\b",
    re.IGNORECASE,
)
_PT_DIACRITICS = re.compile(r"[ãõçáéíóúâêô]")


@dataclass(frozen=True, slots=True)
class BenchmarkPrompt:
    language: str
    task: ModelTask
    prompt: str


# The closed suite: 2 prompts per language, covering the tasks the
# router actually serves. Never extend this at runtime.
BENCHMARK_PROMPTS: tuple[BenchmarkPrompt, ...] = (
    BenchmarkPrompt(
        language="pt",
        task=ModelTask.CHAT,
        prompt=(
            "Responda em portugues em uma frase: o que voce faz neste PC?"
        ),
    ),
    BenchmarkPrompt(
        language="pt",
        task=ModelTask.SUMMARIZE,
        prompt=(
            "Resuma em portugues em uma frase: um PC lento abre varios "
            "programas juntos e disputa memoria."
        ),
    ),
    BenchmarkPrompt(
        language="en",
        task=ModelTask.CHAT,
        prompt=(
            "Answer in English in one sentence: what can you do on this PC?"
        ),
    ),
    BenchmarkPrompt(
        language="en",
        task=ModelTask.CLASSIFY,
        prompt=(
            "Classify in one word whether a slow PC is a hardware or "
            "a software problem."
        ),
    ),
    BenchmarkPrompt(
        language="ja",
        task=ModelTask.CHAT,
        prompt="このPCで何ができますか？一文で答えてください。",
    ),
    BenchmarkPrompt(
        language="ja",
        task=ModelTask.SUMMARIZE,
        prompt="PCが遅い原因を一文で要約してください。",
    ),
)


def language_fidelity(language: str, text: str) -> float:
    """1.0 when the answer really is in the requested language."""
    wanted = str(language or "").strip().lower()
    answer = str(text or "")
    if wanted == "ja":
        return 1.0 if _JA_SCRIPT.search(answer) else 0.0
    if wanted == "en":
        return 1.0 if _EN_WORDS.search(answer) else 0.0
    if wanted == "pt":
        if _PT_WORDS.search(answer) or _PT_DIACRITICS.search(answer):
            return 1.0
        return 0.0
    raise BrainError(f"idioma de benchmark desconhecido: {language!r}")


def score_turn(fidelity: float, tokens_per_s: float) -> float:
    """0-100: fidelity share plus speed saturating at the reference."""
    speed = min(max(float(tokens_per_s), 0.0) / SPEED_REFERENCE_TOKENS_PER_S, 1.0)
    return round(FIDELITY_SHARE * float(fidelity) + SPEED_SHARE * speed, 2)


def _prompts_for(languages: tuple[str, ...]) -> list[BenchmarkPrompt]:
    return [
        item
        for item in BENCHMARK_PROMPTS
        if item.language in languages
    ]


def run_benchmark(
    model_id: str,
    *,
    registry: ModelRegistry = DEFAULT_MODEL_REGISTRY,
    database=None,
    base_url: str = DEFAULT_BASE_URL,
    languages: tuple[str, ...] = LANGUAGE_SET,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run the closed suite against one installed model.

    Requires the loopback runtime to be up and the GGUF to be present;
    every failure is a BrainError with the exact reason. When a database
    is given, each turn is journaled and aggregate scores become
    available to the router via benchmark_scores().
    """
    spec = registry.get(model_id)
    if spec is None:
        raise BrainError(f"modelo nao registrado: {model_id!r}")

    wanted = tuple(
        lang.strip().lower()
        for lang in dict.fromkeys(languages)
        if str(lang).strip()
    )
    unknown = sorted(set(wanted) - set(LANGUAGE_SET))
    if unknown:
        raise BrainError(f"idiomas desconhecidos: {unknown} (validos: {LANGUAGE_SET})")
    if not wanted:
        raise BrainError("nenhum idioma selecionado para o benchmark.")

    if not model_path(spec.filename, models_dir()).is_file():
        raise BrainError(f"GGUF ausente: {spec.filename}")

    probe = probe_server(base_url)
    if not probe.get("reachable"):
        raise BrainError(
            "Runtime indisponivel: inicie o servidor do modelo com "
            f"`python -m zeus_pc.brain.serve --model-id {spec.model_id}`."
        )

    results: list[dict[str, Any]] = []
    for item in _prompts_for(wanted):
        started = time.perf_counter()
        error: str | None = None
        content = ""
        prompt_tokens = 0
        completion_tokens = 0
        try:
            result = chat_complete(
                item.prompt,
                model=spec.model_id,
                base_url=base_url,
                max_tokens=BENCHMARK_MAX_TOKENS,
                temperature=0.0,
                timeout=timeout,
            )
            content = result["content"]
            usage = result["usage"]
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except BrainError as exc:
            error = str(exc)

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        seconds = max(elapsed_ms / 1000.0, 1e-6)
        tokens_per_s = round(completion_tokens / seconds, 2)
        fidelity = language_fidelity(item.language, content) if error is None else 0.0
        score = score_turn(fidelity, tokens_per_s)

        results.append(
            {
                "language": item.language,
                "task": item.task.value,
                "ok": error is None,
                "error": error,
                "elapsed_ms": elapsed_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens_per_s": tokens_per_s,
                "fidelity": round(fidelity, 2),
                "score": score,
            }
        )
        if database is not None:
            database.record_benchmark(
                model_id=spec.model_id,
                language=item.language,
                task=item.task.value,
                elapsed_ms=elapsed_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tokens_per_s=tokens_per_s,
                fidelity=fidelity,
                score=score,
            )

    per_language: dict[str, dict[str, Any]] = {}
    for language in wanted:
        rows = [row for row in results if row["language"] == language]
        if not rows:
            continue
        count = len(rows)
        per_language[language] = {
            "prompts": count,
            "mean_fidelity": round(
                sum(row["fidelity"] for row in rows) / count, 2
            ),
            "mean_tokens_per_s": round(
                sum(row["tokens_per_s"] for row in rows) / count, 2
            ),
            "score": round(sum(row["score"] for row in rows) / count, 2),
        }

    aggregate = (
        round(
            sum(block["score"] for block in per_language.values())
            / len(per_language),
            2,
        )
        if per_language
        else 0.0
    )
    return {
        "model_id": spec.model_id,
        "prompt_count": len(results),
        "score": aggregate,
        "languages": per_language,
        "results": results,
    }
