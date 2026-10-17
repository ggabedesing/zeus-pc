# Increment 008: Installed software and virtualization context

## Objective
Complete the first deep-inventory pass with installed desktop software and Windows virtualization/security context.

## Installed Software
Source: uninstall registry keys in HKLM/HKCU, including 32-bit and 64-bit views.
Fields: DisplayName, DisplayVersion, Publisher, InstallDate, InstallLocation and registry scope.
Policy: never use Win32_Product because it can trigger MSI consistency checks.

## Virtualization
Sources:
- Win32_ComputerSystem HypervisorPresent;
- Win32_Processor VirtualizationFirmwareEnabled and SLAT support;
- Win32_DeviceGuard when available for virtualization-based security state.

## Optional Features
Source: Get-WindowsOptionalFeature -Online.
Fields: FeatureName and State.
This query may require elevation on some systems; that is reported explicitly rather than guessed.

## Safety
Read-only. No package uninstall, feature enable/disable, Hyper-V/WSL mutation or security weakening.

## Privacy
Software inventory remains local in the Zeus database/API. No upload or telemetry is introduced.

## Verification
Mocked tests cover software deduplication/sorting, virtualization aggregation, optional-feature elevation handling and explicit unsupported states.

## Next
Deep inventory baseline is sufficient to move to Observer performance work: lower-overhead sampling, ring buffer and session baselines.
