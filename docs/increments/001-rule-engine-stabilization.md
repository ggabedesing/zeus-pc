# Increment 001: Contextual Rule Engine stabilization

Scope: stabilize PR #1 only. No new optimization action is enabled.

Objective: keep the optional scan profile connected to the read-only diagnostic and recommendation pipeline.

Behavior: GET /api/scan accepts an optional profile and defaults to GENERAL. The response returns the selected profile with the diagnostic snapshot and recommendations.

Architecture: URL parsing stays in the local HTTP boundary. RuleContext owns profile normalization. Windows probes remain read-only.

Permissions: unchanged. Probes that need elevation continue reporting REQUIRES_ELEVATION.

Unknown data: UNKNOWN and NOT_CHECKED are preserved and are never interpreted as healthy.

UI/UX: no visual expansion in this increment. Existing UI remains compatible because added response fields are additive.

Risk: low. This changes request parsing and recommendation context only.

Verification: Windows CI must compile the package and run the unit tests. No Windows state is modified, so runtime rollback is not applicable; source rollback is a normal Git revert.

Next: only after PR #1 is green and integrated, start the persistence/config/logging foundation as separate increments.
