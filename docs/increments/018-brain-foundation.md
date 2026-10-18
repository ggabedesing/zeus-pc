# Increment 018: Brain foundation (Zeus Model Hub)

## Objective
Give the Zeus Agent a local brain: a closed registry of GGUF models, a
loopback-only inference runtime, a deterministic task router and a
`zeus doctor` health report — all offline, stdlib-only, no API keys.

## Scope decision
This increment declares and routes models; it does not download them.
Download, license acceptance and benchmarks arrive in 019 (manager +
bench). Machine mutations remain exclusively behind the Transaction
Engine: the brain only proposes and answers.

## ModelSpec / ModelRegistry (`brain/registry.py`)
- Closed schema like `ActionRegistry`: model_id (`^[A-Z0-9_]+$`),
  filename (plain `.gguf`, no separators — no path escape), quantization,
  size/context/RAM budgets, **license_name (mandatory)**, languages,
  strengths (`ModelTask`: CLASSIFY/SUMMARIZE/REASON/PLAN/CHAT),
  quality_rank, source_url, notes.
- Fail-closed registration: duplicate or malformed specs raise
  `BrainError`.
- Curated lineup of 7 (deterministic, sorted ids): ELYZA_JP_8B,
  QWEN25_7B (the only multilingual/PT reference), SWALLOW_7B,
  LLM_JP_1_8B, STABLELM_JP_7B, RINNA_3_6B, SARASHINA2_7B — each with its
  real license declared (Meta Llama 3, Llama 2, Apache-2.0, MIT).

## Runtime (`brain/runtime.py`)
- `detect_llama_server()` via `ZEUS_LLAMA_SERVER` or PATH — detection
  only, nothing is executed.
- RAM budgets cross-platform (Windows `GlobalMemoryStatusEx`, POSIX
  `sysconf`).
- `assert_loopback()` fails closed: the brain only ever talks to
  127.0.0.1/localhost; non-loopback URLs raise.
- `probe_server()` (GET /health, tolerant of the 503 "loading" state)
  and `chat_complete()` (OpenAI-compatible POST, prompt ≤ 12000 chars,
  max_tokens ≤ 2048, temperature clamped).

## Router (`brain/router.py`)
Deterministic routing, no clock and no randomness: for a task it
considers every registered model in id order and accepts the first with
strength match + GGUF present + RAM fit, then reports *why* each
candidate was skipped (`sem_tarefa`, `sem_arquivo`, `memoria`). Explicit
model choices are strict: any problem raises `BrainError`.

## Invoke (`brain/__init__.py`)
`invoke(task, prompt, ...)` = route → probe → complete, returning
`{task, model_id, routing, content, usage, elapsed_ms}`. Every failure
mode is a `BrainError` with an actionable message (which GGUF is
missing, how much RAM is needed, which command starts the runtime).

## `zeus doctor` (`doctor.py`)
`python -m zeus_pc.doctor` prints a deterministic JSON report:
persistence (schema + write probe), `powercfg` presence, brain server
detection/reachability, models dir with per-model `present`/`fits_ram`.
The CLI bootstraps the DB idempotently, like the server.

## API
- `GET /api/brain/models` → `{version, count, models[]}` (pure
  declaration, no filesystem checks, deterministic).
- `GET /api/brain/status` → the full doctor report.
- `POST /api/brain/invoke` `{task, prompt, model_id?, max_tokens?,
  temperature?}` → `{result}`; `BrainError` maps to **409** with the
  exact reason (400 stays for generic validation).

## Safety
- No shell strings anywhere: the serve helper
  (`python -m zeus_pc.brain.serve --model-id X`) builds an argv list and
  binds 127.0.0.1 only.
- The brain cannot mutate the machine: no path from invoke to
  TransactionEngine.
- Licenses are declared, never accepted implicitly.

## VERIFY
120 unittest cases pass locally (registry closedness, router
determinism/file/RAM guards, loopback rejection, stub-server probe and
chat, invoke end-to-end, doctor report, models-dir regression);
`python -m compileall src` passes; HTTP smoke test confirmed
`/api/brain/models` (count=7), `/api/brain/status` (4 named checks),
`POST /api/brain/invoke` → 409 with the routing reason, and the doctor
CLI. Windows CI remains the merge gate.

## Next
Increment 019 — multi-model manager: GGUF download with hash + license
acceptance (SQLite v4), LRU load/unload with the RAM guard, benchmark
suite in JA/EN/PT and a per-task scoring table feeding the router.
