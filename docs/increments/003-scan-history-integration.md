# Increment 003: Scan history integration

## Objective
Connect the existing SQLite persistence layer to the real diagnostic scan flow.

## Why
Before this increment, Zeus had database primitives but scans were not actually persisted by the server.

## Behavior
- every completed /api/scan is written to scan_history;
- the scan response includes scan_id;
- /api/history returns recent scans with a defensive limit;
- /api/status exposes persistence schema version and writable state;
- server startup initializes the Zeus database.

## Safety
This increment writes only Zeus-owned application data. It does not modify Windows configuration.

## Failure semantics
Persistence is part of the scan transaction boundary for this increment. If persistence fails, the request fails instead of pretending history was saved.

## Verification
- unit test runs a synthetic scan;
- returned scan_id is checked;
- persisted profile, snapshot and recommendation are read back from SQLite;
- Windows CI must pass before integration.

## Continuity
Next planned increment: versioned app configuration and structured logging. Transaction/audit logging remains separate from ordinary application logs.
