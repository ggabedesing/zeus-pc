# Increment 004: Versioned configuration and structured logging

## Objective
Give Zeus a stable application configuration contract and bounded structured logs before deeper automation work.

## Behavior
- config schema version 1;
- selected profile is persisted and validated;
- history limit is persisted and clamped to 1..100;
- observer_enabled is persisted as a boolean;
- partial config updates preserve existing values;
- unknown schema versions reset to safe defaults;
- GET /api/config reads the current config;
- POST /api/config updates only Zeus application preferences;
- scan defaults to the persisted selected profile;
- history defaults to the persisted history limit;
- JSONL application logs record startup, shutdown, config updates, completed scans and request failures;
- log retention is bounded.

## Safety
This increment changes only Zeus-owned local application data. It does not modify Windows state and does not add new privileged actions.

## Separation of concerns
Application logs are not the future Transaction Engine audit journal. Action consent, BeforeState, VERIFY and rollback evidence remain a separate security subsystem.

## Verification
- config defaults;
- partial update preservation;
- invalid profile fallback;
- unknown schema handling;
- log write/read;
- bounded retention;
- empty event rejection;
- scan integration writes a structured completion event;
- Windows CI must pass before merge.
