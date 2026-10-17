# Increment 014: Transaction execution lifecycle

## Objective
Enable the first real mutation path in Zeus PC through the Transaction Engine: resource lock, APPLY, independent VERIFY, journal transitions and automatic rollback when VERIFY fails — for SET_ACTIVE_POWER_PLAN only.

## Scope decision
Execution is enabled per action definition. `ActionDefinition` now carries optional `apply` and `rollback` callables; an action without `apply` stays prepare/consent only. A reversible action without `rollback` refuses to execute. No other action category gained execution in this increment.

## Lifecycle
CONSENTED → APPLYING → APPLIED → VERIFIED

Failure paths:
- APPLY fails → APPLYING → FAILED (verify and rollback are never called);
- VERIFY fails → APPLIED → ROLLING_BACK → ROLLED_BACK;
- VERIFY and ROLLBACK both fail → ROLLING_BACK → FAILED.

Every transition is an atomic conditional UPDATE guarded by the expected previous state, so a concurrent or repeated execute cannot apply twice.

## Resource lock
The engine keeps one in-process lock per `resource_key`. A transaction acquires `POWER_SCHEME` (or another resource lock) before APPLYING, so two mutations of the same resource are serialized while distinct resources stay independent.

## Independent VERIFY
VERIFY re-reads the real system state after APPLY; it never trusts the APPLY result. A VERIFY exception is treated as "not confirmed" and triggers rollback.

## Automatic rollback
On failed VERIFY the engine executes the action's rollback with the BeforeState captured at prepare time and the persisted rollback preview, then records the rollback payload and the error text in the journal.

## Journal (schema v2 → v3)
`transaction_journal` gains `apply_json`, storing what APPLY actually did. Migration is an idempotent `ALTER TABLE` and preserves v1/v2 scan, settings and transaction rows.

## API
- POST /api/transactions/execute {transaction_id} → runs the lifecycle and returns {transaction, state, ok, message};
- GET /api/transactions now reports execution_enabled true and executable_actions;
- /api/status reports execution_mode "TransactionExecution" and enabled_actions;
- POST /api/actions/power-plan stays blocked with 409 (legacy direct path remains disabled).

Consent is still mandatory and single-use: execute only works from CONSENTED, exactly once.

## SET_ACTIVE_POWER_PLAN
- APPLY calls `powercfg /setactive` through the existing validated `set_power_plan`, which re-checks GUID syntax, plan existence and Windows confirmation;
- VERIFY independently re-reads the active plan with `powercfg /list`;
- ROLLBACK restores the BeforeState active GUID and confirms it the same way.

## Safety
- no arbitrary shell, no new command strings;
- no execution without prior one-time consent;
- no execute without rollback strategy for reversible actions;
- failed verify never leaves the machine silently in the new state;
- structured log events: transaction_verified, transaction_rolled_back, transaction_failed.

## VERIFY
80 unittest cases pass locally (including concurrency lock, single-shot execute, failed apply, failed verify + rollback, failed rollback, consent gating and schema v2→v3 preservation); `python -m compileall src` passes; Windows CI remains the merge gate.

## Next
Increment 015: action catalog expansion (startup control / safe cleanup preview) or crash recovery for transactions stuck in APPLYING/ROLLING_BACK after a restart — decided per ROADMAP order.