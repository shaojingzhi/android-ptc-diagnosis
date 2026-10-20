# Waydroid hwcomposer registry-backed workflow smoke

## Frozen setup

- implementation commit: `a783d5ae21868e09120a87f26c8c7678572ea333`
- neutral incident ID: `incident-public-android-container-02`
- input: exact upstream Logcat lines 5,148–5,304 from Waydroid issue #1894
- input events / compact groups: 157 / 73
- model: `deepseek-v4-pro`, temperature 0, thinking disabled
- provider retries: 0; maximum tool-calling rounds: 6
- model-visible upstream issue, fix, and assessment: none
- runs: one; no retry or prompt adjustment

The source attachment, excerpt, prompt, and tool-schema hashes are recorded in `trace.jsonl`. The trace
contains no API credential. This is a single public-case smoke, not an accuracy or stability estimate.

## Execution metrics

| Metric | Result |
| --- | ---: |
| final status | accepted |
| model turns | 4 |
| tool-calling rounds | 3 |
| tool calls | 6 |
| input tokens | 120,794 |
| output tokens | 2,901 |
| total tokens | 123,695 |
| total latency | 33,282.9 ms |
| final validation errors | none |

The model issued four indexed `get_events` calls covering all 157 records, then one `filter_by_time`
and one `inspect_failure_context` call. The higher-level primitive inspected 30 bounded records around
eight candidates, but it was used only after the complete input had already been materialized. The run
therefore demonstrates diagnosis behavior, not efficient bounded retrieval.

## Diagnosis outcome

The accepted final explicitly selected the `hwcomposer.waydroid.so` null-pointer crash as the primary
failure candidate. It identified:

- composer process PID 363, SIGSEGV/SEGV_MAPERR, and null-pointer cause;
- `wl_proxy_marshal` and `hwc_set` as the first two frames;
- SurfaceFlinger `DEAD_OBJECT`/SIGABRT as downstream;
- the later system-process `Surface$OutOfResourcesException: DEAD_OBJECT` as propagation;
- the read-only cgroup errors as a coexisting condition rather than the initiating graphics crash.

This is materially better attribution than the unchanged FIXED baseline, whose Top-1 is event 19's
`createProcessGroup(...): Read-only file system`; the null-pointer record is only FIXED Top-3. The model
did not name the exact missing `display->pointer` source field, which was not present in its evidence.
It did recommend checking the Wayland display/socket state, consistent with the independently hidden
upstream fix.

## Claim audit

1. **HWC SIGSEGV/null dereference in `wl_proxy_marshal` via `hwc_set`: supported.** The cited handles
   resolve to events 33, 36, 37, 44, and 45.
2. **HWC crash was the earliest failure in the cycle: only partially supported.** Tombstone timestamps
   place it before the SurfaceFlinger abort and the later visible cgroup errors, but the bounded capture
   begins inside a repeating boot loop and merged Logcat buffers are not evidence that no earlier failure
   existed. “Earliest” is too strong.
3. **SurfaceFlinger DEAD_OBJECT as downstream of composer death: reasonable hypothesis.** Events 85,
   95, and 96 show the failed HIDL call and HWC-facing stack. The evidence supports propagation but does
   not prove strict causality by itself.
4. **System-process Surface DEAD_OBJECT: supported observation.** Events 154–156 directly contain the
   fatal exception and native call site.
5. **Repeated cgroup and missing-interface observations: supported.** The excerpt contains multiple
   instances and restarts.
6. **Read-only cgroup is a persistent independent configuration defect affecting every service: partly
   overclaimed.** The evidence shows several failures in this excerpt, not every service or persistence
   outside the capture. Independence from the HWC crash is confirmed only by the hidden upstream fix,
   not by the model-visible logs alone.
7. **HWC crash as primary with graphics-stack propagation: well supported as a candidate.** Preventing
   startup completion remains an inference grounded in the supplied symptom and repeated failure cycle.

All 26 cited handles existed in the run registry and resolved to evidence actually shown to the model.
Format/reference acceptance therefore succeeded. It does not make the two overbroad semantic statements
fully entailed.

## Recommended-check audit

The Wayland socket/compositor and image-compatibility checks are useful and discriminative. Symbolized
source inspection would be needed to identify the exact null pointer. Two cgroup suggestions are less
focused; one mixes inspection with “correct it to rw,” which is remediation rather than a read-only
diagnostic check and is not justified as the incident fix. This should be reported as a limitation, not
silently corrected after the run.

## Bottom line

On this one real public incident, the workflow found the correct failure component and native failure
site despite earlier, repeated ERROR records that mislead the generic FIXED ranking. Independent upstream
evidence confirms the missing-Wayland-pointer mechanism. The run did not infer that exact source field,
overstated global chronology and cgroup scope, and read the full event set at high token cost. It is a
useful auditable success case, not proof of general Android-container diagnosis accuracy.
