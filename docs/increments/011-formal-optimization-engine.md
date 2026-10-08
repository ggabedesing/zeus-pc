# Increment 011: Formal Optimization Engine (dry-run)

## Objective
Create the first formal optimization-plan layer that converts diagnostic rules plus Observer evidence into typed, explainable, non-executing findings.

## Why
Recommendations currently exist as a flat list. Before Zeus can safely execute anything, it needs a stable contract that separates evidence, confidence, risk, benefit, applicability, automation readiness and blockers.

## Inputs
- diagnostic snapshot (ProbeResult domains);
- selected profile;
- Observer baseline;
- latest process-performance snapshot when available.

## Output
OptimizationPlan:
- unique plan_id and created_at;
- normalized profile;
- mode=DRY_RUN;
- typed findings;
- summary counts by status;
- explicit execution_ready=false for this increment.

OptimizationFinding:
- id/title/category;
- formal status: APPLY_RECOMMENDED, ALREADY_CORRECT, NOT_APPLICABLE, BLOCKED, MANUAL_REVIEW or NO_ACTION;
- reason;
- benefit;
- confidence HIGH/MEDIUM/LOW;
- risk;
- reversible;
- optional semantic action_id;
- structured evidence;
- blockers.

## Rule bridge
Existing contextual rules remain the source of current machine recommendations. The formal engine wraps them rather than duplicating their logic. Existing RECOMMENDED maps to APPLY_RECOMMENDED in dry-run; MANUAL_REVIEW and ALREADY_CORRECT preserve their semantics.

## Observer interpretation
Observer evidence is only used when sample coverage is sufficient.
- sustained CPU >= 85% average with at least 3 CPU samples => MANUAL_REVIEW performance finding;
- sustained RAM >= 90% average with at least 3 RAM samples => MANUAL_REVIEW memory finding;
- gaming/streaming GPU >= 95% average with at least 3 GPU samples => NO_ACTION explanatory GPU-bound finding, not a blind tweak.
If process context exists, top CPU/memory process names and values are attached as evidence.

## Safety
- DRY_RUN only;
- no new mutable action;
- no action endpoint added;
- no performance claim without measured evidence;
- GPU saturation alone is not labeled a defect;
- missing Observer metrics are ignored, never converted to zero.

## API
GET /api/optimization/plan uses the latest persisted diagnostic snapshot when available. If no history exists, it performs a diagnostic scan first. It returns the formal plan and does not execute it.

## VERIFY
Tests cover:
- recommendation-to-formal-status mapping;
- no-action preservation;
- deduplication;
- minimum Observer sample coverage;
- CPU/RAM observed pressure;
- GPU-bound explanatory NO_ACTION;
- process evidence attachment;
- plan summary/execution_ready=false.

## Next
Increment 012: Optimization Engine compatibility/conflict layer and machine-specific applicability before Transaction Engine work begins.
