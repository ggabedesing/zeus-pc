# Increment 005: Deep inventory — services, scheduled tasks and processes

## Objective
Expand the read-only machine inventory with three operational Windows domains that are required before Zeus can make safe startup, service, maintenance and performance decisions.

## Why
Optimization decisions are unsafe when Zeus cannot see what is running, which services are active, or which scheduled tasks exist. This increment adds visibility only; it does not disable, stop, prioritize or modify anything.

## Domains
### Services
Source: Win32_Service through CIM.
Fields: Name, DisplayName, State, StartMode, StartName, ProcessId and PathName.
Metrics: total count, running count, automatic-start count and full inventory.

### Scheduled Tasks
Source: Get-ScheduledTask.
Fields: TaskPath, TaskName and State.
Metrics: total count, disabled count and full inventory.

### Processes
Source: Win32_Process through CIM.
Fields: Name, ProcessId, ParentProcessId, WorkingSetSize, ExecutablePath, ThreadCount and HandleCount.
Metrics: total count, total working-set bytes, top memory consumers and full inventory.

## Permissions
Normal user context. Fields that Windows does not expose without additional rights remain null; Zeus must not reinterpret null as healthy or unhealthy.

## Safety
Read-only. No Stop-Service, Set-Service, Disable-ScheduledTask, Stop-Process, priority change or affinity change is enabled.

## Failure semantics
Each domain fails independently. A failed task query does not invalidate service or process inventory.

## UI/API
The new domains are added to the existing diagnostic snapshot and therefore become available to technical views without a new mutation endpoint.

## Verification
- mocked service inventory counts;
- mocked scheduled-task counts;
- mocked process aggregation and top-memory ordering;
- collect_all includes all three domains;
- Windows CI must pass before merge.

## Next
Continue deep inventory with TPM, firewall/proxy/link information and storage health/reliability before any new optimization action is enabled.
