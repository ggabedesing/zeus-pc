# Zeus PC — Development Progress

Updated: 2026-10-17 (brain foundation added)

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
Increment 018 — Brain foundation: Zeus Model Hub (registry + runtime + router + doctor).

Status:
- branch: freebuff/0a766cbf2b57fadb3e6fa501;
- src/zeus_pc/brain/ package created (registry.py, runtime.py, router.py, serve.py);
- ModelSpec/ModelRegistry closed schema (mandatory license, languages, strengths, RAM/context budgets) with 7 curated GGUF models: ELYZA_JP_8B, QWEN25_7B (the only PT/multilingual reference), SWALLOW_7B, LLM_JP_1_8B, STABLELM_JP_7B, RINNA_3_6B, SARASHINA2_7B;
- loopback-only runtime: llama-server detection (ZEUS_LLAMA_SERVER/PATH), RAM budgets (Win32/POSIX), /health probe tolerant of the loading 503, OpenAI-compatible chat_complete with prompt/token caps, non-loopback URLs rejected fail-closed;
- deterministic router: strength match → GGUF present → RAM fit, with per-candidate skip reasons (sem_tarefa/sem_arquivo/memoria); explicit model choice is strict;
- invoke() = route → probe → complete; every failure is a BrainError with an actionable message;
- python -m zeus_pc.doctor prints a JSON report (persistence, powercfg, brain server, models dir with present/fits_ram per model);
- GET /api/brain/models (pure declaration), GET /api/brain/status (doctor report), POST /api/brain/invoke (409 with the exact routing/runtime reason);
- serve helper builds an argv list bound to 127.0.0.1 — no shell strings anywhere;
- 120 unit tests pass locally; increments 014-016 already on origin/main with Windows CI green (run 39077102802).

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
Increment 019 — multi-model manager: GGUF download with hash verification + license acceptance (SQLite v4), LRU load/unload under the RAM guard, JA/EN/PT benchmark suite whose scores feed the router. Then 020: the agent loop consuming context + brain; local MCP server and crash recovery stay queued in parallel.

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
