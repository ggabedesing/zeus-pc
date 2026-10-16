# Increment 014B — Real-machine hardening

## Goal
Make Zeus PC work on real hardware, not only in unit tests. Every domain was
re-read on the developer machine and each mismatch was fixed at its source.

## Findings and fixes

### Probe errors were leaking raw engine text
`probes.py`, `platform_inventory.py`, `software_platform.py` and
`storage_health.py` built messages with `str(exc)`, so the UI showed
PowerShell argv, category lines and English engine text.
Replaced with `humanize_probe_error(exc, source, default_status)` from
`models.py`, which classifies into four families:

| Family | Tokens | Status returned |
| --- | --- | --- |
| Elevation | access is denied, acesso negado, eleva, administ, permiss | REQUIRES_ELEVATION |
| Timeout | tempo esgotado, timed out, timeout | PARTIAL |
| Unsupported | is not recognized, nao e reconhecido, invalid namespace, not available | UNSUPPORTED |
| Other | anything else | caller default, usually FAILED |

Message text is operator-facing Portuguese and never contains the command.
The three duplicate local `_error_status` helpers were deleted.

### Optimistic OK statuses
A probe that read nothing still reported OK. Added `demote_if_unknown(result,
meaningful, fallback_status)` which demotes OK to PARTIAL when every meaningful
metric is `None`. Applied to TPM, Firewall, Proxy, Devices, Windows Features.
A real zero, an empty list or `False` stays OK; only the unknown demotes.

### Windows Features reported FAILED without admin
`Get-WindowsOptionalFeature -Online` needs elevation, so the domain was FAILED
on an ordinary session. Switched the data source to the CIM class
`Win32_OptionalFeature`, readable unelevated:

```powershell
Get-CimInstance Win32_OptionalFeature | Select-Object Name,Caption,InstallState
```

`InstallState` maps to `Enabled` (1), `Disabled` (2), `Not Present` (3) and
`Unknown` for any other value. The domain now reads 136 features and 13 enabled
ones on the real machine with no elevation, and keeps REQUIRES_ELEVATION only
if the CIM read itself fails.

### PowerShell console encoding
PowerShell emitted OEM cp850 while the parser expected UTF-8, corrupting
accented values. `run()` now prefixes
`[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); `, captures bytes,
decodes UTF-8 with `errors="replace"`, scrubs error echo lines through
`_clean_error`, and maps `TimeoutExpired` to exit 124 with a clean message and
`OSError` to 127.

### powercfg plan names
`powercfg /list` also writes OEM. `actions.py` dropped `text=True` and decodes
through `decode_console_output`, which tries `oem` then `utf-8` and falls back
safely. Verified on the real machine: `Desempenho Máximo` decodes to U+00E1,
no replacement characters, and the journal stores the accented BeforeState.

### Windows Update timeout
The WUA COM search regularly exceeded the 12 second budget. Raised to 25 seconds
and the timeout path now yields PARTIAL with a clean message instead of a raw
engine dump. The domain reads OK on the real machine.

### UI had no path to the only real mutation
Increment 014 added execution but the front end could not reach it. `web/app.js`
grew the Energia panel into the full flow: plan list, prepare, consent card
showing the target plan, the current plan labelled as what a rollback restores,
a live consent countdown and an acknowledgement checkbox that gates the confirm
button; then execute, and honest VERIFIED, ROLLED_BACK and FAILED rendering,
including `resource_locked`. The consent token lives only in a JS variable and
is never written to storage. A journal table reads from `/api/transactions`.

## Real-machine result after hardening
Full `collect_all()` on Windows 11 with an ordinary unelevated session:

- 25 domains OK;
- Secure Boot REQUIRES_ELEVATION, TPM and Storage Reliability PARTIAL, Integrity NOT_CHECKED;
- zero FAILED domains;
- active plan read back as `e3614da8` (Desempenho Máximo) throughout;
- a UI prepare that was cancelled stayed PREPARED in the journal with no
  `consent_consumed_at`, no apply evidence and no system change.

## Tests
114 tests green. `tests/test_powershell.py` (7) and `tests/test_probe_hardening.py`
(17) are new modules; coverage in `tests/test_actions.py` and
`tests/test_software_platform.py` was reworked.
`test_optional_features_counts_enabled` was rewritten for the CIM contract,
which is an intentional interface change rather than a regression.

## Boundaries kept
Read-only probes, no generic shell, no `shell=True`, strict GUID regex before
`powercfg`, consent hashed and single-use, VERIFY always an independent fresh
read, server bound to 127.0.0.1.
