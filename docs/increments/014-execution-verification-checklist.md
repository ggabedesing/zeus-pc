# Increment 014 verification gates

- Require one-time consent before execution.
- Reject stale BeforeState.
- Record APPLY and independent VERIFY evidence.
- Attempt rollback on failed verification.
- Keep resource locked if rollback is unverified.
- Do not merge before Windows CI passes.
