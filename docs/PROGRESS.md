# Zeus PC — Development Progress

Updated: 2026-10-18 (desktop UI overhaul + honest PowerShell decoding)

## Completed foundations
- Clean repository and Windows CI.
- Read-only core diagnostics.
- Extended diagnostics for drivers, devices, network and pending reboot.
- Contextual Rule Engine with explicit profiles.
- SQLite persistence with schema versioning, settings and scan history.
- Real scan history integration with scan_id and /api/history.
- Versioned application configuration with persisted profile/history preferences.
- Bounded structured JSONL application logging.
- Read-only deep inventory for Windows hardware, services, tasks, processes, security, network, storage, events, software and virtualization.
- Native CPU/RAM Observer sampling, bounded history/baselines, background runner, sessions and process-level context.
- Formal dry-run Optimization Engine with typed findings, evidence/confidence, compatibility gates and action-collision detection.

## Current increment
Increment 022 — Desktop UI overhaul (readable, no raw JSON) + honest PowerShell output decoding.

Status:
- web/index.html + web/styles.css rewritten desktop-first: fixed 216px sidebar, dense system-tool typography, five pages (Painel, Meu PC, Desempenho, Otimizar, Sistema); the mobile bottom bar only appears below 760px;
- zero JSON.stringify in the normal flow — every probe renders as label→value pairs (pt-BR labels, formatted /Date(..)/ dates, bytes in GB, booleans as Sim/Não, lists summarized with "+N outros"); raw JSON stays only behind the opt-in "Modo técnico" drawer;
- home opens with the last scan from /api/history (verdict, 6 machine chips, recommendations, brain status from doctor) instead of an empty screen; live CPU/GPU/RAM/temp polling at 3s;
- "Meu PC" groups 30 domains into 6 sections; "Otimizar" translates blockers/benefit/risk/confidence to Portuguese; "Sistema" tiles render label→value rows;
- backend fix: powershell.py run() captures bytes and _decode_output() tries UTF-8 → console OEM (GetOEMCP) → ANSI → latin-1; actions.py (list_power_plans/set_power_plan) and probes.py (power_probe) no longer use text=True — powercfg wrote cp850 (á=0xA0) while text mode read ANSI (0xA0=NBSP), so "Desempenho Máximo" reached the API as "Desempenho M\xa0ximo";
- 187 unit tests pass (6 new in tests/test_powershell_decoding.py); compileall OK;
- real proof: GET /api/power-plans assert "Desempenho Máximo" (codepoint 0xe1) passes on the restarted server; browser DOM check: 0 <pre> across all 5 pages, 30 domain cards, plan counts 1/0/0/0, clean console with 9× sample 200.

## Previous increment (021)
Increment 021 — Walking skeleton runner: one command proving the full real cycle on Windows.

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- src/zeus_pc/skeleton.py + `python -m zeus_pc.skeleton` CLI: fixed step order doctor → model (license-gated install + SHA-256 verify) → serve (llama-server subprocess, argv only, /health wait, always terminated) → invoke (one measured turn) → transaction (switch power plan and restore the original, both through prepare → consent → execute);
- every step ends ok | falha | pulado with the exact reason; dependent steps skip when a prerequisite did; exceptions become failures (_safe_step), never tracebacks; exit code 1 only on falha;
- failed restore is reported as FAILA CRITICA in the step detail; report carries counts and per-step extras (sha256, states, transaction ids);
- 181 unit tests pass locally (13 new in tests/test_skeleton.py, including real two-transaction switch/restore on a temp DB with a power-plan-like action);
- CLI smoke: default run reports 4 pulados and exit 0; unknown model reports falha and exit 1.

Real Windows PC run (2026-10-18, Python 3.12.10):
- 181 unit tests pass on the PC itself;
- python -m zeus_pc.skeleton → doctor ok (brain_server/models_dir observed), model/serve/invoke pulado (no GGUF), transaction ok: Equilibrado switch + Alto desempenho restore, both VERIFIED, counts {ok:2, falha:0, pulado:3}, exit 0;
- independent confirmation: powercfg /getactivescheme reports the restored "Alto desempenho" scheme;
- local server verified on 127.0.0.1:8000 (/api/status schema v4, /, /styles.css, /app.js, /favicon.svg all 200);
- still pending: llama-server install + a real GGUF download so model/serve/invoke stop skipping.

## Previous increment (020)
Increment 020 — API hardening: loopback-only Host, same-origin Origin and JSON Content-Type guards, SQLite WAL, UI polish.

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- guards in server.py: Host must be loopback on every method (DNS rebinding), Origin must match loopback + server port on mutations (browser CSRF, incl. Origin: null), body of any POST must be application/json (blocks no-cors text/plain) — rejections close the connection and are audited as request_rejected;
- non-browser clients keep working (curl with JSON, scripts); app.js only issues GETs so the UI is unaffected;
- persistence: journal_mode=WAL + busy_timeout=5000 on every connection (fallback when unsupported) and _ensure_schema() so reads no longer rewrite the schema;
- UI polish: favicon.svg, meta description, descriptive title, clickable logo, custom web/404.html for static routes (API stays JSON 404);
- 168 unit tests pass locally (15 new in tests/test_api_hardening.py, including guards exercised through the real Handler over HTTP);
- HTTP smoke verified: Host/Origin/Content-Type rejections (403/403/415), JSON reaching the domain layer, 404 HTML vs JSON, WAL active, 4 audit events.

## Previous increment (019)
Increment 019 — Model Manager: licensed GGUF install with SHA-256, LRU storage under the RAM guard, JA/EN/PT benchmark feeding the router.

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- SQLite schema v4: model_installs (license acceptance, sha256, source, last_used_at) and model_benchmarks (per-turn metrics); benchmark_scores() = latest row per (model, language), averaged;
- brain/manager.py: ModelManager with mandatory license acceptance recorded in SQLite; HTTPS download limited to an allowlisted host set, streamed to .part, SHA-256 verified before rename (mismatch leaves nothing); local import copies without touching the user's file; verify detects tampering (ok/alterado/ausente/nao_rastreado);
- LRU under RAM guard: keep order = fits RAM first, then most recently used (touched on every successful invoke), model_id tie-break; over-budget models become eviction candidates with exact reasons (memoria/lru); prune only deletes manager-tracked files and only with explicit confirmation;
- brain/benchmark.py: closed 6-prompt suite (2 per language ja/en/pt), language-fidelity score (60%) + throughput saturating at a 20 tok/s CPU reference (40%); every turn journaled;
- router tie-break: measured benchmark score beats the static quality_rank deterministically (medido > declarado); invoke passes scores and touches the LRU;
- API: POST /api/brain/install (license gate, 409 with exact reason), POST /api/brain/verify, GET /api/brain/storage (installs + LRU plan + scores), POST /api/brain/prune (confirm required), POST /api/brain/benchmark; invoke now routes with measured scores;
- doctor: new model_storage and brain_benchmarks checks plus storage/benchmark_scores blocks;
- 153 unit tests pass locally (33 new in tests/test_model_manager.py); two old schema assertions fixed in intent (assertGreaterEqual(SCHEMA_VERSION, 3)); HTTP smoke validated the full install → benchmark → invoke-with-scores → LRU touch cycle against a loopback OpenAI-compatible stub.

## Previous increment (018)
Increment 018 — Brain foundation: Zeus Model Hub (registry + runtime + router + doctor).

Status:
- src/zeus_pc/brain/ package created (registry.py, runtime.py, router.py, serve.py);
- ModelSpec/ModelRegistry closed schema (mandatory license, languages, strengths, RAM/context budgets) with 7 curated GGUF models: ELYZA_JP_8B, QWEN25_7B (the only PT/multilingual reference), SWALLOW_7B, LLM_JP_1_8B, STABLELM_JP_7B, RINNA_3_6B, SARASHINA2_7B;
- loopback-only runtime: llama-server detection (ZEUS_LLAMA_SERVER/PATH), RAM budgets (Win32/POSIX), /health probe tolerant of the loading 503, OpenAI-compatible chat_complete with prompt/token caps, non-loopback URLs rejected fail-closed;
- deterministic router: strength match → GGUF present → RAM fit, with per-candidate skip reasons (sem_tarefa/sem_arquivo/memoria); explicit model choice is strict;
- invoke() = route → probe → complete; every failure is a BrainError with an actionable message;
- python -m zeus_pc.doctor prints a JSON report (persistence, powercfg, brain server, models dir with present/fits_ram per model);
- GET /api/brain/models (pure declaration), GET /api/brain/status (doctor report), POST /api/brain/invoke (409 with the exact routing/runtime reason);
- serve helper builds an argv list bound to 127.0.0.1 — no shell strings anywhere;
- 120 unit tests passed; Windows CI green on commit 1086277.

## Previous increment (016)
Increment 016 — Tool catalog: ToolSpec + JSON Schema + GET /api/agent/tools.

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- src/zeus_pc/agent/tools.py with ToolParameter/ToolSpec/ToolCatalog;
- 6 read tools with binding names and JSON Schema parameters;
- mutation tools generated from the ActionRegistry, published only with a declared arg_schema;
- ActionDefinition gained optional arg_schema (SET_ACTIVE_POWER_PLAN declares the exact GUID schema);
- risk_for_action extracted as public API shared by planner and catalog;
- GET /api/agent/tools returns {version, count, tools[]}; catalog is deterministic and rejects duplicate names;
- 98 unit tests passed locally; pushed and validated by Windows CI.

## Previous increment (015)
Increment 015 — Agent foundation: deterministic context + closed-schema planner.

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- src/zeus_pc/agent/ package created (context.py, planner.py);
- build_context() produces a deterministic token-bounded digest (hardware, diagnostico, oportunidades, observacao) with a truncated flag;
- validate_plan() enforces a strict payload schema (1-4 steps, strict keys, registry allowlist, args re-validated by the Transaction Engine validators);
- steps carry explicit risk (MEDIUM/HIGH) and consent_required=true; plan carries max_risk;
- prepare_plan() journals steps as PREPARED transactions without executing;
- GET /api/agent/context (409 without a scan) and POST /api/agent/plan (validation only) added;
- no model, no network, no third-party dependency in this increment;
- 90 unit tests passed locally; pushed and validated by Windows CI.

## Previous increment (014)
Increment 014 — Transaction execution lifecycle (SET_ACTIVE_POWER_PLAN only).

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- ActionDefinition gained optional apply and rollback callables; actions without apply stay prepare/consent only;
- in-process resource lock per resource_key serializes executions of the same resource;
- execute() runs CONSENTED → APPLYING → APPLIED → VERIFIED with guarded atomic transitions;
- independent VERIFY failure triggers automatic rollback to the captured BeforeState;
- failed APPLY → FAILED without calling verify/rollback; failed rollback → FAILED with explicit error text;
- execution is single-shot per transaction and requires a consumed one-time consent;
- SQLite schema upgraded from v2 to v3 with non-destructive apply_json on transaction_journal;
- POST /api/transactions/execute added; /api/status reports execution_mode TransactionExecution;
- structured log events transaction_verified, transaction_rolled_back and transaction_failed;
- 80 unit tests pass locally; waiting for Windows CI before merge.

## Previous increment (013)
Increment 013 — Transaction Engine foundation.

Status:
- branch: auto/transaction-foundation;
- SQLite schema upgraded from v1 to v2 with non-destructive transaction_journal;
- transaction journal stores semantic action, state, normalized args, BeforeState, verify/rollback payloads, expiry and errors;
- consent plaintext is never persisted; only SHA-256 is stored;
- atomic one-time consent consumption added;
- transaction states formalized from PREPARED through VERIFIED/ROLLBACK/FAILED/EXPIRED;
- semantic ActionDefinition and ActionRegistry added;
- initial SET_ACTIVE_POWER_PLAN definition validates exact GUID and existing plan;
- power-plan BeforeState captures active GUID/name;
- independent power-plan VERIFY and rollback-preview interfaces added;
- TransactionEngine.prepare creates journaled PREPARED transaction and short-lived consent token;
- consume_consent validates state, expiry and token with constant-time comparison, then atomically consumes it;
- consent does not execute an action in this increment;
- GET /api/transactions, POST /api/transactions/prepare and POST /api/transactions/consent added;
- legacy direct POST /api/actions/power-plan is now blocked with HTTP 409;
- /api/status reports no enabled mutations and lists preparable semantic actions;
- tests added for prepare/BeforeState/journal, one-time consent, bad/expired tokens, unknown actions, strict args, VERIFY interface and schema v1→v2 data preservation;
- waiting for Windows CI validation before merge.

## Next increment
The skeleton already ran on the real Windows PC (transaction half proven: real powercfg switch + verified restore). Remaining: install llama-server and download a real GGUF so model/serve/invoke run. Then Increment 023 — the agent loop consuming context + brain; local MCP server and crash recovery stay queued in parallel.

## Later order
1. Complete Transaction Engine execution/rollback/crash recovery.
2. Safe action catalog.
3. Drivers and updates.
4. Repair Center.
5. Gaming and streaming optimization.
6. Premium UI.
7. PC Experience.
8. Packaging and release.

## Rule
One coherent increment at a time. Do not advance while CI is broken.
