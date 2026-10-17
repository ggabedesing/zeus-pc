# Increment 010A: Observer background runner and sessions

## Objective
Turn the Observer from request-driven snapshots into a real bounded background monitor with explicit session context and adaptive cadence.

## Why
The previous increment removed PowerShell overhead from CPU/RAM sampling and added a ring buffer, but samples were still collected only when the UI requested them. That is not enough for game/streaming baselines or before/after validation.

## Background runner
- one daemon thread owned by Zeus;
- starts only when observer_enabled is true;
- uses Event.wait so stop is prompt and clean;
- records into the existing bounded ObserverStore;
- never creates an unbounded queue or second sampler thread;
- status exposes running state, current interval and last sample time.

## Adaptive cadence
Pure policy:
- high activity (CPU or GPU >= 60%): 1 second;
- moderate activity (CPU or GPU >= 25%): 2 seconds;
- idle/light activity: 5 seconds;
- if utilization is unknown: 2 seconds.
The policy changes only sampling cadence; it never changes Windows settings.

## Sessions
- a single active session at a time;
- generated UUID;
- label and mode are sanitized;
- samples collected during a session carry session_id;
- start/stop timestamps are retained in memory;
- starting a new session while one is active returns the existing session instead of silently destroying evidence.

## API
- GET /api/observer/status
- POST /api/observer/session/start
- POST /api/observer/session/stop
- /api/observer/sample records through the runner so session_id is preserved.

## Configuration lifecycle
- server startup syncs runner with observer_enabled;
- POST /api/config starts/stops the runner when observer_enabled changes;
- server shutdown stops the runner.

## Safety
Read-only monitoring only. No priority, affinity, service, network, power or scheduler mutation.

## Verification
Tests cover adaptive interval policy, runner start/stop, session lifecycle, session tagging and duplicate-session protection. Windows CI must pass before merge.

## Deferred subpart 010B
Process-level performance context at a lower cadence, with bounded cost and explicit source/confidence.
