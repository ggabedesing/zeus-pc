# Zeus PC — Development Progress

Updated: 2026-10-16

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

## Real-machine hardening (014B)
Status: complete on branch `auto/transaction-power-execution`.

- probe failures now go through `humanize_probe_error`, so messages are
  operator-facing and never leak PowerShell argv or engine text;
- `demote_if_unknown` stops probes that read nothing from claiming OK;
- Windows Features reads `Win32_OptionalFeature` through CIM and works without
  elevation, replacing the previous FAILED domain;
- Windows Update timeout raised to 25s and its failure path made clean;
- PowerShell output forced to UTF-8 and `powercfg` output decoded as OEM, so
  accented plan names such as `Desempenho Máximo` survive into the UI and the
  journal;
- the Energia panel in the web UI now drives the full transaction flow:
  prepare, consent card with countdown and acknowledgement gate, execute, then
  honest VERIFIED / ROLLED_BACK / FAILED reporting plus the journal table;
- real unelevated scan: 25 domains OK, Secure Boot REQUIRES_ELEVATION, TPM and
  Storage Reliability PARTIAL, Integrity NOT_CHECKED, zero FAILED;
- a cancelled UI prepare stayed PREPARED with no consent consumed and no system
  change; the active plan remained `e3614da8` throughout;
- details in `docs/increments/014b-real-machine-hardening.md`.

## Real dashboard (015)
Status: complete on branch `auto/transaction-power-execution`.

Scope set directly by the owner: layout, charts and functions, all real and
functional. The planned transaction crash-recovery increment was postponed to 016.

- `ZeusDatabase.get_scan`, `GET /api/scan/<id>` and `GET /api/report` (standalone
  HTML report, escaped, attachment download) — all read-only;
- observer always live with a 2s/1s/pause control and history backfill;
- charts rewritten (DPR-aware canvas, grid, gradient area, peak label) for CPU,
  GPU, RAM and GPU temperature;
- Top CPU / Top memória process tables with real per-process usage and an honest
  first-sample empty state;
- Meu PC renders 30 domain cards with honest status pills and metric tables;
- Otimizar renders the formal optimization plan (evidence, confidence, risk,
  blockers) that was previously API-only;
- new Histórico page lists stored analyses and reopens any of them;
- exportação de relatório em três pontos da interface;
- latest analysis is restored after a page reload;
- validated in a real browser with zero console errors; 119 tests green;
- details in `docs/increments/015-real-dashboard.md`.

## Next increment
Increment 016A — Local API browser boundary implemented on the current branch:
- exact loopback Host/port validation including default HTTP port;
- matching Origin/Referer, Fetch Metadata checks and anti-framing headers;
- per-server ephemeral API session, held only in browser memory;
- strict JSON object parsing, bounded payloads and socket receive timeout;
- authenticated browser requests and report downloads without mutation replay;
- 141 Python tests run (140 passed, one Windows-codec skip) and six Node tests passed;
- independent review fix for port 80; real HTTP client flow verified;
- browser visual verification unavailable (Playwright Chromium not installed);
- details in `docs/increments/016a-local-api-boundary.md`; pending fresh Windows CI.

Next: Increment 016B — Transaction crash recovery and durable resource ownership
before expanding the safe action catalog.

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
