# Zeus PC — Development Progress

Updated: 2026-10-08

## Completed foundations
- Clean repository and Windows CI.
- Read-only core diagnostics.
- Extended diagnostics for drivers, devices, network and pending reboot.
- Contextual Rule Engine with explicit profiles.
- SQLite persistence with schema versioning, settings and scan history.
- Real scan history integration with scan_id and /api/history.
- Versioned application configuration with persisted profile/history preferences.
- Bounded structured JSONL application logging.
- Read-only inventory for services, scheduled tasks and processes.
- Read-only inventory for TPM, Firewall profiles, proxy and network link state.
- Read-only storage health, reliability, TRIM and bounded Windows event summaries.
- Installed software inventory plus virtualization, hypervisor, SLAT, VBS and Windows optional-feature context.
- Native CPU/RAM Observer sampling, bounded ring buffer, history and baseline APIs.
- Background Observer runner, adaptive cadence and explicit session lifecycle.

## Current increment
Increment 010B — Native process performance context.

Status:
- branch: auto/observer-process-context;
- native Toolhelp32 process enumeration added;
- GetProcessTimes deltas provide per-process CPU share without PowerShell;
- PID reuse is guarded by process creation time;
- K32/PSAPI working-set memory is collected when access is available;
- CPU timing can still be collected when process memory access is denied;
- protected/inaccessible processes are counted explicitly instead of reported as zero;
- process context is sampled at a slower 15-second cadence;
- only bounded top CPU/top memory records are retained;
- process snapshot history is bounded and session-filterable;
- process snapshots inherit active observer session_id;
- GET /api/observer/processes added;
- no command lines, process contents, priority, affinity or termination actions are collected/enabled;
- tests added for CPU delta math, PID reuse, top-N ordering, bounded history and session tagging;
- waiting for Windows CI validation before merge.

## Next increment
Increment 011 — Formal Optimization Engine: typed findings and dry-run optimization plans consuming inventory plus Observer evidence.

## Later order
1. Formal Optimization Engine.
2. Transaction Engine.
3. Safe action catalog.
4. Drivers and updates.
5. Repair Center.
6. Gaming and streaming optimization.
7. Premium UI.
8. PC Experience.
9. Packaging and release.

## Rule
One coherent increment at a time. Do not advance while CI is broken.
