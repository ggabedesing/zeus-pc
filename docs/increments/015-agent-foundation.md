# Increment 015: Agent foundation (context + planner)

## Objective
Start transforming Zeus PC into a local AI agent without third-party services: give it a deterministic, bounded "what it knows" digest (context) and a closed-schema planner that can turn an untrusted proposal into validated steps.

This increment contains no model and no execution: everything is deterministic and testable without inference.

## Context (src/zeus_pc/agent/context.py)
`build_context()` assembles a token-bounded digest from state Zeus already collects:
- hardware (CPU/GPU/RAM/SO from the scan snapshot);
- diagnostico (domain status + up to 6 metrics per domain, strings capped at 120 chars);
- oportunidades (only APPLY_RECOMMENDED findings with action, risk and confidence);
- observacao (observer baseline min/avg/max per metric).

Properties:
- deterministic: same inputs produce the same prompt;
- budget: default 3000 estimated tokens (len/4), floor of 200; sections are added in priority order and cut/dropped when the budget runs out, setting `truncated`;
- `to_prompt()` renders markdown for a local model; `to_dict()` exposes sections, budget and prompt for the API/UI.

## Planner (src/zeus_pc/agent/planner.py)
`validate_plan(payload, registry, profile)` converts an untrusted payload into an `AgentPlan`:
- payload must be an object with only {steps, explanation};
- 1..MAX_PLAN_STEPS (4) steps, each a strict object with only {action_id, args, rationale};
- action_id must exist in the semantic ActionRegistry (no free commands, ever);
- args must pass the same strict validator used by the Transaction Engine; validator exceptions (TransactionError/ValueError/TypeError) become PlanError;
- each step carries risk (USER + reversible -> MEDIUM; anything else -> HIGH) and consent_required=true;
- the plan carries max_risk and a deterministic uuid/timestamp.

`prepare_plan(engine, plan)` journals every step as a PREPARED transaction via the Transaction Engine; nothing executes.

## API
- GET /api/agent/context -> context for the latest scan; 409 when no scan exists yet;
- POST /api/agent/plan -> validation-only endpoint that returns the normalized plan (or 400 with the exact reason). Structured log event: agent_plan_validated.

Both endpoints are read-only/validation-only: no mutation path was added.

## Safety
- no LLM, no network, no third-party dependency;
- planner cannot introduce new actions: the registry is the allowlist;
- planning never touches Windows state except read-only validators;
- execution remains exclusively in the Transaction Engine lifecycle (014).

## VERIFY
90 unit tests pass (context determinism, budget truncation, prompt content, plan normalization, risk/consent derivation, unknown action/extra keys/bad shapes rejection, power-plan GUID validation via the default registry, prepare-plan journaling); compileall OK; HTTP smoke test covers 409 without scan, context with sections/budget, and 400 on unknown action.

## Next
Increment 016 — local inference runtime: embed a quantized open-weight model (llama.cpp/GGUF shipped from this project's own releases) behind a small `runtime.py`, plus an offline chat endpoint that consumes build_context(); no API keys, no telemetry.
