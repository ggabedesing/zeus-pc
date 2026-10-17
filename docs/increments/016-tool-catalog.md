# Increment 016: Tool catalog (ToolSpec + JSON Schema)

## Objective
Give the Zeus Agent a public, deterministic contract for its capabilities: a tool catalog in JSON Schema that any consumer (local model, Codex, Obsidian via future MCP) can read before proposing anything. Inspired by the contract/catalog pattern of mature MCP servers (e.g. morluto/rea), but self-contained: no third-party service, no network.

## ToolSpec (src/zeus_pc/agent/tools.py)
Two tool families:

- **read tools** (zeus_status, zeus_scan, zeus_context, zeus_history, zeus_power_plans, zeus_observer_baseline) — declarative descriptors with `binding` names that the agent loop will resolve to read-only callables; parameters carry JSON Schema fragments (type, description, pattern, enum, default).
- **mutation tools** — generated from the semantic ActionRegistry. A mutation is published only when its ActionDefinition declares an `arg_schema` (JSON Schema), and it always carries:
  - `action_id` link back to the registry;
  - `risk` (risk_for_action: USER + reversible -> MEDIUM, else HIGH);
  - `consent_required: true`;
  - description stating the Transaction Engine lifecycle (prepare, unique consent, APPLY, independent VERIFY, automatic rollback).

`input_schema()` returns `additionalProperties: false` for generated read schemas, and the action's declared object schema for mutations.

## ActionDefinition.arg_schema (src/zeus_pc/transaction.py)
New optional declarative field; it documents arguments for the catalog and never replaces `validate_args` at execution time. SET_ACTIVE_POWER_PLAN declares the exact GUID schema using `GUID_RE.pattern` (single anchored pattern — a double-anchor bug caught by the HTTP smoke test was fixed before commit).

## Determinism and safety
- catalog is a pure function of the registry: sorted names, no clock, no randomness; two builds are byte-equal (tested);
- duplicate tool names raise ToolCatalogError (an action named ZEUS_STATUS colliding with the read tool is tested);
- actions without arg_schema are not published (fail closed, not open);
- catalog exposes contracts only — it executes nothing.

## API
GET /api/agent/tools -> {version, count, tools[]} with input_schema per tool.

## VERIFY
98 unit tests pass (catalog contents, strict JSON Schema shape, GUID pattern anchoring, schema-less action exclusion, determinism, duplicate rejection, high-risk derivation, parameter schema generation); compileall OK; HTTP smoke test shows count=7 with `set_active_power_plan` (MEDIUM, consent) and anchored GUID pattern.

## Next
Increment 017 — local MCP server exposing this catalog (stdio/HTTP on 127.0.0.1) for Obsidian/Codex, or the agent loop with read bindings; local inference runtime follows as 018.
