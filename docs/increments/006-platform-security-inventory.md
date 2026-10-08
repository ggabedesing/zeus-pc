# Increment 006: Platform and security inventory

## Objective
Add read-only visibility for TPM, Windows Firewall profiles, current-user proxy configuration and physical/network link state.

## Why
Zeus must understand platform security and network context before making any recommendation involving Windows security, connectivity or latency. Missing context must never be treated as permission to weaken security.

## Domains
### TPM
Source: Get-Tpm.
Fields: presence, readiness, enabled/activated state and auto-provisioning state where Windows exposes them.
Policy: diagnostic only. No Clear-Tpm, ownership change or provisioning mutation.

### Firewall
Source: Get-NetFirewallProfile.
Fields: profile name, enabled state and default inbound/outbound policy.
Policy: diagnostic only. Zeus never disables firewall for performance.

### Proxy
Source: current-user Internet Settings registry.
Fields: ProxyEnable, ProxyServer, AutoConfigURL and AutoDetect when exposed.
Policy: this is user proxy context, not proof of all WinHTTP/system proxy state.

### Network Link
Source: Get-NetAdapter.
Fields: adapter identity, status, link speed, MAC, driver information and physical media type where exposed.
Policy: diagnostic only. No adapter reset, DNS change or advanced-property mutation.

## Permissions and UNKNOWN
Normal user context is preferred. If Windows reports access denied, the domain reports REQUIRES_ELEVATION. Unsupported cmdlets/features report UNSUPPORTED where identifiable. Null properties remain unknown.

## Security invariants
- Never disable Defender, Firewall, Secure Boot or TPM for performance.
- Never infer that a missing TPM/firewall value means disabled.
- No DNS/proxy/network changes are enabled.

## Verification
Mocked tests for TPM, firewall profile aggregation, proxy normalization, link counts and error-status classification. Windows CI must pass before merge.

## Next
Storage health/reliability/TRIM and event summaries, then Observer performance work.
