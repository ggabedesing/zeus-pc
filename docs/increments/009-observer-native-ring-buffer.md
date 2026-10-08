# Increment 009: Observer native sampling and ring buffer

## Objective
Replace the high-overhead CPU/RAM PowerShell sampling path with native Windows APIs and add a bounded in-memory observation buffer with baseline summaries.

## Why
The existing Observer launches PowerShell processes for CPU and RAM every sample. That is too expensive for frequent monitoring and can distort the machine state being measured.

## Sampling
### CPU
Source: GetSystemTimes through ctypes.
Method: compute busy percentage from deltas between idle/kernel/user cumulative FILETIME values.
First native call has no previous baseline and therefore returns cpu_pct=null rather than inventing a value.

### RAM
Source: GlobalMemoryStatusEx through ctypes.
Method: read dwMemoryLoad directly.

### GPU
Keep the existing nvidia-smi fallback in this increment. GPU optimization/caching is a later subpart.

## Ring Buffer
- bounded deque, default 300 samples;
- thread-safe append/read;
- newest samples retained;
- no unbounded memory growth;
- process-local only in this increment.

## Baseline
For each available metric, return count/min/max/average over the selected recent samples.
Missing values are skipped, not converted to zero.

## API
- /api/observer/sample: collect and record one sample;
- /api/observer/history?limit=N: recent samples;
- /api/observer/baseline?limit=N: aggregate baseline.

## Permissions
No elevation required for native CPU/RAM sampling.

## Safety
Read-only. No process priority, affinity, power-plan or scheduler mutation.

## Verification
Tests cover CPU delta math, ring-buffer retention, missing-value baseline handling and server-facing observer store behavior. Windows CI must pass before merge.

## Next
Add adaptive sampling/session boundaries and process-level performance context, then feed Observer evidence into the formal Optimization Engine.
