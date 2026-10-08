# Increment 014: Transaction execution lifecycle — power plan only

## Objective
Enable the first real Windows mutation through the Transaction Engine, and only for SET_ACTIVE_POWER_PLAN, with resource locking, APPLY, independent VERIFY, persistent state transitions and automatic rollback when verification fails.

## Scope
Exactly one semantic action is executable: SET_ACTIVE_POWER_PLAN.

## Preconditions
- transaction must exist;
- transaction state must be CONSENTED;
- action must be registered;
- normalized arguments and BeforeState must already be journaled;
- resource lock POWER_SCHEME must be available.

## Action contract
ActionDefinition gains:
- apply(args): performs the mutation but does not claim success beyond the command result;
- existing verify(args): independently re-reads Windows state;
- rollback remains derived from the captured BeforeState/rollback preview.

For power plans:
- APPLY uses powercfg /setactive only;
- VERIFY uses a fresh powercfg /list read;
- rollback applies the BeforeState active GUID;
- rollback is independently verified with another fresh read.

## State machine
CONSENTED -> APPLYING -> APPLIED -> VERIFIED

On APPLY failure:
CONSENTED -> APPLYING -> FAILED

On VERIFY failure:
APPLIED -> ROLLING_BACK -> ROLLED_BACK when rollback verifies.

If rollback cannot be applied or verified, state becomes FAILED with explicit rollback evidence/error. Zeus never reports VERIFIED in that case.

## Locking
A process-local semantic ResourceLockManager prevents two POWER_SCHEME transactions from executing concurrently. Cross-process/crash recovery locking remains a later Transaction Engine subpart.

## Idempotency
- VERIFIED returns the existing verified transaction without applying again;
- terminal FAILED/ROLLED_BACK/EXPIRED/CANCELLED states cannot execute;
- PREPARED cannot execute before consent;
- APPLYING/APPLIED/Rolling-back states are rejected for manual recovery in this increment.

## API
POST /api/transactions/execute with transaction_id.

The legacy /api/actions/power-plan remains blocked.

## Status
/api/status may list SET_ACTIVE_POWER_PLAN in enabled_actions only after this lifecycle is integrated.

## Safety
- no arbitrary shell;
- no raw command endpoint;
- strict GUID validation;
- one-time consent already required;
- independent verification;
- automatic rollback on verify failure;
- every transition persisted.

## VERIFY
Tests cover happy path, apply failure, verify-failure rollback success, rollback failure, missing consent, idempotent VERIFIED, resource-lock contention and power apply command isolation.

## Next
Increment 015: transaction crash-recovery/host-origin request hardening and durable resource ownership before expanding the safe action catalog.
