# Zeus PC — Development Progress

Updated: 2026-10-13

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
- Transaction Engine foundation: journalized prepare, one-time consent, semantic registry, BeforeState capture and independent VERIFY interfaces.

## Current increment
Increment 014 — Transaction execution lifecycle for SET_ACTIVE_POWER_PLAN.

Status:
- branch: auto/transaction-power-execution;
- SQLite schema upgraded from v2 to v3 with non-destructive `apply_json` column on `transaction_journal`;
- `ActionDefinition` gained an optional `apply` callable plus an `executable` property, and `ActionRegistry.executable_ids()`;
- `SET_ACTIVE_POWER_PLAN` APPLY calls `powercfg /setactive` only, with strict GUID validation and no shell;
- VERIFY stays a fresh independent `powercfg /list` read, never the apply result;
- rollback is derived from journaled BeforeState and independently verified with another fresh read;
- process-local `ResourceLockManager` serializes mutations per `resource_key` (POWER_SCHEME);
- `TransactionEngine.execute` implements CONSENTED -> APPLYING -> APPLIED -> VERIFIED with every transition persisted;
- APPLY failure moves the transaction to FAILED and releases the resource;
- VERIFY failure moves APPLIED -> ROLLING_BACK -> ROLLED_BACK when rollback applies and verifies;
- unconfirmed rollback moves to FAILED and keeps the resource locked for manual recovery;
- stale BeforeState is rejected before APPLY and the transaction stays CONSENTED;
- VERIFIED replays are idempotent and never apply twice; PREPARED, in-flight and terminal states are rejected;
- POST /api/transactions/execute added; rejected executions return HTTP 409 with the reason;
- /api/status now reports `execution_mode: TransactionExecution` and lists SET_ACTIVE_POWER_PLAN as an enabled action;
- legacy POST /api/actions/power-plan remains blocked with HTTP 409;
- tests added for the happy path, APPLY failure, VERIFY-failure rollback, unconfirmed rollback locking, missing consent, idempotent VERIFIED, resource contention, stale BeforeState, unregistered executor and powercfg command isolation;
- validated locally on real Windows hardware: prepare, consent, execute and idempotent replay ended VERIFIED with persisted apply and verify evidence;
- waiting for Windows CI validation before merge.

## Next increment
Increment 015 — Transaction crash recovery, host-origin request hardening and durable resource ownership before expanding the safe action catalog.

## Later order
1. Safe action catalog.
2. Drivers and updates.
3. Repair Center.
4. Gaming and streaming optimization.
5. Premium UI.
6. PC Experience.
7. Packaging and release.

## Rule
One coherent increment at a time. Do not advance while CI is broken.

Increment 014 implementation is under review; Windows CI is required before merge.

014A: APPLY primitive and schema v3 on branch; execution not yet wired.
