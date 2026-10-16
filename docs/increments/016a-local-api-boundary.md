# Increment 016A — Local API browser boundary

## Scope and design

First correction from the October 16 audit: protect the existing loopback HTTP
flow before adding new system actions. The server remains bound to 127.0.0.1.
Every request must carry the actual port and either the exact 127.0.0.1 or
localhost Host. Supplied Origin and Referer must match that Host. Cross-site
Fetch Metadata and embedded loads are rejected before reading configuration,
collecting metrics or dispatching a transaction.

GET /api/session bootstraps a cryptographically random server-lifetime token.
All other /api/ requests require X-Zeus-Session. Bootstrap is subject to the
same origin/host checks, has no CORS allowance and is not cacheable. Credentials
stay in JavaScript memory, not URLs, localStorage or the database. This boundary
protects against browser cross-origin requests; it does not authenticate OS
users or isolate the API from another local program that can bootstrap a token.

POST requires a UTF-8 application/json object and exactly one nonnegative
decimal Content-Length, between 1 and 65536 bytes. Transfer-Encoding, duplicate
critical headers, malformed JSON and non-standard JSON constants are rejected.
Connections have a ten-second I/O timeout. Responses block framing and MIME
sniffing. These changes do not add a concurrency limit or a total request
deadline; those remain separate resource-control work.

web/api.js shares a bootstrap between concurrent callers, sets the session
header and refuses foreign destinations and redirects. A 403 invalidates its
cached token for subsequent requests. Mutations are never replayed automatically.
The existing report links use authenticated fetch and a temporary Blob URL.

## Implementation plan and verification ledger

- [x] Reproduce missing host/origin/session controls with real loopback HTTP tests.
- [x] Implement guard and strict body reader in http_security.py; integrate before
  GET and POST dispatch, plus per-server ephemeral tokens.
- [x] Test concurrent bootstrap, POST preservation, foreign destination rejection,
  bootstrap recovery, no mutation replay and authenticated report content.
- [x] Integrate browser requests and report downloads; add Node tests to both CI jobs.
- [x] Run full Python suite, Node tests, JavaScript syntax and Python compilation.

Local results: 141 Python tests run, 140 passed and one Windows-codec test skipped;
six Node tests passed. Original HTTP regression suite failed before the fix and
passed after it. No Windows system action was executed during verification.

Independent review found default-port omission (Host/Origin on port 80); added a
regression and fixed the allowlist before publishing. Extra tests cover duplicate
headers, truncated bodies, receive timeouts and per-server token rotation.
The JavaScript client was also exercised against a real loopback server through
bootstrap, status, diagnostic scan, history and HTML report retrieval. Browser
visual verification was unavailable because the Playwright Chromium executable
is not installed in this environment; no claim of visual verification is made.

## Remaining order

016B: durable resource ownership and startup recovery of interrupted transactions.
Then: lock-before-BeforeState validation, uncertain APPLY reconciliation,
checked journal transitions and execution consent expiry. New sensor integrations
and action families stay after the transaction corrections.
