# Increment 002: Persistence foundation

Scope: add local persistence without changing Windows state.

Objective: give Zeus a durable place to store settings and scan history before transaction and audit work begins.

Behavior:
- initialize a local SQLite database idempotently;
- track schema version;
- store JSON settings by key;
- record completed diagnostic snapshots and recommendations;
- read recent scan history with a defensive limit.

Architecture: standard-library sqlite3 only. The persistence layer is independent from probes, rules, UI, and mutable actions.

Permissions: normal user write access to the chosen local path. No elevation is required.

Failure model: persistence errors are separate from Windows diagnostic health. A database failure must never be reported as a healthy scan.

Risk: low. This increment writes only Zeus-owned local data.

Verification: schema version, settings round-trip, scan-history round-trip, Windows CI.

Rollback: source-level revert plus deletion of the local Zeus database if the user explicitly chooses to reset app data.

Next: wire persistence into the scan lifecycle, then add versioned configuration and structured logging.
