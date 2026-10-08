# Increment 013: Transaction Engine foundation

## Objective
Create the safety boundary required before Zeus can execute optimization actions: semantic action registry, validated prepare phase, captured BeforeState, one-time consent, persistent journal and verification/rollback interfaces.

## Security decision
This increment intentionally does NOT execute Windows mutations. The legacy direct power-plan mutation endpoint is blocked so the only future path to mutation must pass through the Transaction Engine.

## ActionDefinition
Each action definition declares:
- semantic action_id;
- resource lock key;
- required privilege;
- reversibility;
- strict argument validator;
- BeforeState capture function;
- independent VERIFY function;
- rollback-preview builder.

No ActionDefinition accepts arbitrary command strings.

## Initial registry
SET_ACTIVE_POWER_PLAN only:
- input: exact GUID;
- validates GUID syntax and that the plan exists;
- captures current active power-plan GUID/name;
- VERIFY independently re-reads active plan;
- rollback preview points back to the captured active GUID.

The apply operation is deliberately absent in this increment.

## Transaction journal
SQLite schema v2 adds transaction_journal:
- transaction_id;
- action_id;
- state;
- normalized args;
- BeforeState;
- verify/rollback payloads;
- consent token hash only, never plaintext;
- expiry/consumed timestamp;
- errors;
- created/updated timestamps.

Migration is idempotent and preserves v1 scan/settings data.

## One-time consent
prepare() issues a cryptographically random token and persists only SHA-256.
consume_consent():
- requires PREPARED state;
- constant-time hash comparison;
- rejects expired tokens;
- consumes token once and transitions to CONSENTED;
- a second use is rejected.
CONSENTED still does not execute anything in this increment.

## API
POST /api/transactions/prepare
POST /api/transactions/consent
GET /api/transactions?limit=N

The old POST /api/actions/power-plan returns 409 and cannot mutate Windows.

## States
PREPARED, CONSENTED, APPLYING, APPLIED, VERIFIED, FAILED, ROLLING_BACK, ROLLED_BACK, EXPIRED, CANCELLED.

## VERIFY and rollback
Interfaces are defined and tested, but APPLY/ROLLBACK execution is not enabled yet.

## Safety
- no arbitrary shell;
- strict semantic allowlist;
- no plaintext consent tokens at rest;
- no mutation endpoint;
- no action execution before Transaction Engine execution/rollback subparts are validated.

## VERIFY
Tests cover schema v1→v2 preservation, prepare/journal, strict args, unknown action rejection, BeforeState capture, one-time consent, expiry, bad token rejection and safe legacy endpoint policy.

## Next
Increment 014: transaction execution lifecycle for SET_ACTIVE_POWER_PLAN only — resource lock, APPLY, independent VERIFY, journal transitions and rollback on failed verify; still no other action category.
