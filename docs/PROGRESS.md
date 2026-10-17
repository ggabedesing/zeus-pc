# Zeus PC — Development Progress

Updated: 2026-10-17

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
Increment 015 — either crash recovery for transactions stuck in APPLYING/ROLLING_BACK after restart, or the first expansion of the safe action catalog (startup control with preview, or safe cleanup preview), per ROADMAP order.

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
