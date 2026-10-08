# Increment 012: Optimization compatibility and conflict gates

## Objective
Add machine/applicability gates and conflict detection to the formal dry-run Optimization Engine before any Transaction Engine exists.

## Why
A recommendation with an action_id is not automatically safe to execute. Zeus must prove that the semantic action is allowlisted, required diagnostic domains are healthy, the profile is compatible, risk/reversibility/confidence requirements are met, and no other finding collides with the same action.

## Static action policy
The first policy registry contains SET_ACTIVE_POWER_PLAN only. It declares:
- required diagnostic domains: Windows and Power;
- allowed profiles;
- maximum risk LOW;
- reversibility required;
- HIGH confidence required;
- security_preserving flag.

Unknown action IDs are blocked rather than passed through.

## Readiness states
Each finding receives a derived readiness record:
- NON_ACTIONABLE: no action_id;
- MANUAL_REVIEW: finding is not APPLY_RECOMMENDED;
- STATIC_ELIGIBLE: all pre-transaction gates pass;
- BLOCKED: one or more gates fail.

Even STATIC_ELIGIBLE findings remain automation_eligible=false and gain TRANSACTION_ENGINE_NOT_AVAILABLE until the Transaction Engine is implemented and verified.

## Conflict detection
Distinct findings that target the same semantic action_id are conservatively treated as ACTION_COLLISION. Both receive a conflict blocker and require manual/engine resolution before execution.

## Compatibility gates
- action must exist in policy registry;
- finding status must be APPLY_RECOMMENDED;
- profile must be allowed;
- required domains must exist and be Status.OK;
- confidence must meet policy;
- risk must not exceed policy;
- reversibility must satisfy policy;
- security-category actions require an explicitly security-preserving policy.

## Output
OptimizationPlan.summary gains a compatibility report with:
- transaction_engine_ready=false;
- static_eligible_count;
- executable_count=0;
- blocked_action_count;
- non_actionable_count;
- conflict_count;
- conflicts;
- per-finding readiness.

## Safety
No action execution, consent flow or new mutation endpoint is introduced.

## VERIFY
Tests cover safe static eligibility, missing/failed domains, unknown actions, confidence/risk/reversibility gates, security policy gate and action collision detection.

## Next
Increment 013: Transaction Engine foundation — semantic ActionDefinition registry, prepare/BeforeState, one-time consent contract, journal and VERIFY interfaces, still with execution restricted until the foundation is tested.
