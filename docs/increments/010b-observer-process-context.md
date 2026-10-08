# Increment 010B: Native process performance context

## Objective
Add bounded process-level performance evidence to the Observer without returning to high-frequency PowerShell/CIM polling.

## Why
System-wide CPU/RAM/GPU metrics can show that a bottleneck exists, but not which process is responsible. Gaming/streaming diagnosis needs top process CPU and memory context.

## Native source
Windows Toolhelp32 + process APIs through ctypes:
- CreateToolhelp32Snapshot / Process32FirstW / Process32NextW for process enumeration;
- OpenProcess + GetProcessTimes for cumulative CPU time;
- K32GetProcessMemoryInfo for working set when access is available.
Protected/inaccessible processes remain counted but are not treated as zero usage.

## CPU calculation
Per-process CPU percentage is derived from process kernel+user time deltas divided by elapsed wall time and logical processor count. The first observation of a PID has cpu_pct=null. PID reuse is guarded by creation time.

## Cost control
- process context sampled at a slower cadence than system metrics;
- default process cadence: 15 seconds;
- only top CPU and top memory records are retained;
- process history is bounded in memory;
- no command-line arguments are collected;
- no process contents are inspected.

## Session context
Process snapshots collected during an Observer session carry the same session_id.

## API
GET /api/observer/processes returns the latest bounded process snapshot/history.

## Safety
Read-only. No process terminate, suspend, priority or affinity mutation.

## Verification
Pure tests cover CPU delta math, PID reuse handling, top-N ordering and bounded process history. Runner tests verify process snapshots inherit session_id. Windows CI must pass before merge.

## Next
Formal Optimization Engine: typed optimization findings/plans that consume inventory + Observer evidence without executing changes.
