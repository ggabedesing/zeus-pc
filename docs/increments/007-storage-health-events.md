# Increment 007: Storage health, TRIM and event summaries

## Objective
Add read-only storage reliability context and bounded Windows event summaries so Zeus can distinguish capacity cleanup from actual device/system stability problems.

## Domains
### Physical Storage
Source: Get-PhysicalDisk.
Fields include friendly name, serial, media type, bus type, size, health status and operational state.

### Storage Reliability
Source: Get-StorageReliabilityCounter per physical disk when Windows exposes it.
Metrics include temperature, wear, power-on hours and read/write error counters when available.
If counters are unavailable, Zeus reports PARTIAL rather than inventing health.

### TRIM
Source: fsutil behavior query DisableDeleteNotify.
Zeus reads NTFS/ReFS delete-notify state only. It never runs retrim/defrag in this increment.

### Windows Events
Source: Get-WinEvent.
Window: last 24 hours, bounded to 100 Critical/Error events from System and Application logs.
The diagnostic stores IDs/providers/timestamps/levels, not full event messages, to keep history bounded.

## Permissions
Normal user where possible. Access-denied results are explicit. Missing Storage cmdlets are UNSUPPORTED where identifiable.

## Safety
Read-only. No format, chkdsk repair, Optimize-Volume, Clear-Disk, firmware command, event-log clearing or storage mutation.

## Verification
Mocked tests cover disk health inventory, partial reliability data, TRIM parsing and event critical/error counts. Windows CI must pass before merge.

## Next
Finish deep inventory with software inventory and Windows feature/virtualization context, then move to Observer performance work.
