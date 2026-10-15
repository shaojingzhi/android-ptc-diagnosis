# Paired container-startup model evaluation

## Scope

This report records one frozen batch of registry-backed Compact-DIRECT runs over four paired
container-startup incidents. Each pair has a complete and a deliberately limited evidence version.
The eight runs are paired inputs, not eight independent samples. The data is generated or adapted,
not production data, and the batch has no independent holdout set.

The experiment asks whether the model finds distinguishing evidence, how far it can bound a failure,
whether it preserves unknowns when evidence is absent, and where failures occur. It does not measure
general accuracy or production readiness.

## Frozen setup

- Case data commit: `a86fc6b37955837f203a9e585d46f7552c7cbd69`.
- Runner implementation: `82ed471418e5364637ba347d7d395a5ed3367fa4`.
- Freeze manifest: `experiments/container-startup-paired-eval/manifest.json`.
- Model: DeepSeek `deepseek-v4-pro`, temperature 0, thinking disabled.
- Maximum tool-calling rounds: 6; provider SDK retries: 0.
- Executor: opt-in registry-backed Compact-DIRECT with the existing typed-reference nine-tool
  surface. Every run used a fresh executor, conversation, and EvidenceItem registry.
- The system prompt hash was
  `044817fafbddd2432d64d526df6b6fb2d2d4bbb913b3c90bf7c012b72169a451`.
- The actual tool schema hash was identical in all eight runs:
  `400117aa54d610522a0a18de86e0466a2500d15752ca49aadc18fe5fc4786097`.
  Runtime inventories changed tool results, not the generic schema bytes.

The fixed, mechanism-independent order below was committed before any model response. The evaluator
mapping was never placed in the model task. Task prompts were checked to exclude the case directory,
pair/version metadata, source ledger, expected conclusions, and scoring criteria. `expected.yaml`
was used only after the runs for human review.

1. `startup-eval-01` → dependency limited
2. `startup-eval-02` → mount full
3. `startup-eval-03` → namespace limited
4. `startup-eval-04` → cgroup OOM full
5. `startup-eval-05` → mount limited
6. `startup-eval-06` → dependency full
7. `startup-eval-07` → cgroup OOM limited
8. `startup-eval-08` → namespace full

The manifest contains every model-input file hash, each task-prompt hash, the evaluation-only
`expected.yaml` hash, and the execution order. Trace hashes were checked against it after the batch.

## Raw execution metrics

Missing usage would be reported as unknown rather than zero. Usage was available for every run.

| Incident | Evaluation-side case | Final status | Turns | Tool rounds | Tool calls | Input | Output | Total tokens | Latency |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 01 | dependency limited | accepted | 5 | 4 | 7 | 46,118 | 2,533 | 48,651 | 37.28s |
| 02 | mount full | accepted | 4 | 3 | 6 | 47,277 | 2,019 | 49,296 | 28.19s |
| 03 | namespace limited | JSON parse failed | 2 | 1 | 2 | 14,337 | 294 | 14,631 | 5.19s |
| 04 | cgroup OOM full | accepted | 5 | 4 | 6 | 62,822 | 2,048 | 64,870 | 30.10s |
| 05 | mount limited | accepted | 4 | 3 | 8 | 46,275 | 2,512 | 48,787 | 34.90s |
| 06 | dependency full | reference validation failed | 4 | 3 | 10 | 50,084 | 2,765 | 52,849 | 37.45s |
| 07 | cgroup OOM limited | accepted | 4 | 3 | 5 | 42,447 | 2,337 | 44,784 | 35.57s |
| 08 | namespace full | accepted | 4 | 3 | 5 | 50,023 | 1,807 | 51,830 | 27.28s |
| **Total** | 8 independent runs | **6 accepted** | **32** | **24** | **49** | **359,383** | **16,315** | **375,698** | **235.96s** |

Six accepted outputs are a format/reference result, not a diagnosis-quality score. Incident 03 kept
the non-JSON raw text; incident 06 kept its parsed diagnosis and the exact invalid reference path.
There were no provider errors, executor crashes, or exhausted tool budgets, and no run was repeated.

## Pair audit

### OverlayFS mount preparation

**Full.** Exact mount arguments, Logcat `EINVAL`, and the KMessage constraint were already present in
initial compact groups, including
`log_event:event:4:compact_group:9efccb...` and
`log_event:event:11:compact_group:89c755...`. The model then actively acquired differentiating
runtime state: after one invalid `overlay` mount reference, it discovered the mount inventory and
read `/state/upper` (`mount_id=41`, ext4) and `/scratch/work` (`mount_id=52`, tmpfs). It correctly
bounded the failure to root-filesystem mount preparation and the upper/work mount mismatch, did not
claim an OverlayFS kernel defect, and explicitly noted that KMessage monotonic time and Logcat wall
time do not establish cross-source order. However, the raw preamble said “This is the root cause,”
which is stronger than the project permits. Active acquisition helped confirm the path identities;
the error mechanism itself was already initially visible.

**Limited.** The initial evidence showed only incomplete rootfs preparation, init not started, and a
readiness timeout. The model read all events, bounded context, process/service/mount inventories,
and the `/run/container/root` record. The returned field
`runtime_state:mount:/run/container/root:preparation_error_details:6fb0ed...` was exactly
`not_collected`. Its accepted final did not invent OverlayFS, `EINVAL`, or the hidden path mismatch;
it kept the underlying preparation error unknown. The first check (“inspect RootfsPrepare logs”) is
useful but less discriminating than directly capturing exact mount arguments/return and upper/work
mount IDs, which appeared secondarily as storage/mount inspection. Those observations are absent
from the replay snapshot, so the existing tool cannot recover them.

**Boundary:** full reaches a concrete mount-configuration candidate; limited reaches the rootfs
preparation stage only. Limited failure sources are missing data and unavailable replay observation,
not a wrong final attribution.

### Local cgroup OOM

**Full.** The decisive evidence was initially visible: before/after `memory.events.local`,
`oom_kill=1` (`log_event:event:5:compact_group:a4c572...`), host `MemAvailable`, the process exit,
and the KMessage kill record (`log_event:event:13:compact_group:ca33d1...`). The model expanded log
context and process state, but its cgroup call used the path `/runtime/startup-02` where the replay
API expected the cgroup record name, so it never read the cgroup runtime-state object. Its diagnosis
still correctly distinguished a local cgroup OOM from host-wide pressure, but this was primarily an
initial-log hit rather than successful active cgroup inspection. A recommendation to “consider
raising” `memory.max` is remediation rather than a read-only diagnostic check.

**Limited.** The model saw SIGKILL/137, a configured 32 MiB limit, and the timeout. It made the same
invalid path-based cgroup query, then read process state. It did not claim direct confirmation:
the medium-confidence OOM hypothesis explicitly requested a kernel OOM record, before/after memory
events, and peak usage. Its first next check would distinguish the remaining explanations. Still,
calling exit 137 a “canonical OOM-kill signature” overstates specificity because another actor can
send SIGKILL. The decisive counters and kill record were not collected; even a correctly named
cgroup query would return `not_collected` fields.

**Boundary:** full can identify a local cgroup limit event; limited can identify a killed startup
process and an OOM hypothesis, but must keep the signal source unresolved. The limited run shows both
a real data gap and a wrong locator choice, not a proven OOM diagnosis.

### External dependency readiness

**Limited.** Initial logs said only that a required dependency was not ready and detail collection
was unavailable. The model actively discovered process/service identities and inspected
`runtime_service`, `dependency_service`, and `runtime_init`. The registry records
`runtime_state:service:dependency_service:observation_status:d0d330... = not_collected` plus
`runtime_service` dependency/connection observation fields that are also `not_collected`. The final
kept the dependency identity, direct state, and connection result unknown and prioritized identifying
the dependency and inspecting its startup/registration and connection path. Its phrase “never became
ready” goes slightly beyond a not-collected observation, although it was a medium-confidence
hypothesis with explicit missing evidence.

**Full.** The model actively discovered and inspected both processes and both services. It obtained
`ECONNREFUSED`, `host_bridge state=starting`, `ready=false`, `listener_bound=false`, and
`registration_state=loading`; these observations materially support narrowing the investigation to
bridge readiness while leaving “why registration remains loading” unresolved. The recovered final
is semantically useful, but it is formally rejected: one timeout ID was mistyped. The actual ID is
`log_event:event:10:compact_group:00ef6596baf07bd7de6ab94f26596f338ad6dfca5dae0963e7e590d46cd6dacb`;
the model returned a different hash. The trace records path `claims[6].evidence_refs[0]`. Two unrelated
cgroup lookups also failed. This is an output/reference failure despite good evidence selection and
interpretation, so it does not count as accepted.

**Boundary:** the full evidence can reach an unready bridge/listener and connection path, but not the
bridge-internal reason it remains loading. The limited evidence can only establish an unresolved
dependency wait.

### User-namespace allocation limit

**Limited.** The model read all events and bounded context. Its next natural-language response said
it wanted to inspect runtime entities but did not emit a tool call or JSON final, so the run ended as
`json_parse_failed`. There is no diagnosis, uncertainty list, or prioritized next check to score.
The raw response is preserved. The decisive syscall/errno, capability probes, namespace limit, and
policy observations were not collected, and the current tool surface has no live kernel-capability
inspection. This run therefore combines missing data, a tool-observation gap, and an output-format
failure; no hidden-mechanism guess can be attributed to a nonexistent final.

**Full.** The exact `clone3` flags/`ENOSPC`, `CONFIG_USER_NS=y`, proc support, and
`max_user_namespaces=0` were initially visible, for example
`log_event:event:2:compact_group:a88199...` and
`log_event:event:4:compact_group:09c66d...`. The model only added process state; there was no active
kernel-state query. It correctly located the primary candidate at the host namespace allocation
limit rather than claiming missing kernel support or a kernel bug. However, it said seccomp and
security policy were ruled out merely from `seccomp_clone3=allow` and no observed LSM denial. That
does not exclude all permission/policy paths. It also recommended raising the limit and retrying,
which is remediation, not a diagnostic next check.

**Boundary:** full reaches a host configuration/resource limit candidate; it does not cover kernel
defects and does not justify excluding every policy explanation. Limited produced no final and cannot
be credited with uncertainty handling.

## Failure attribution

The same run can have more than one failure type.

| Failure type | Observed evidence |
| --- | --- |
| Data not collected | All four limited inputs intentionally lack their decisive observations. |
| Existing tools cannot expose the needed observation | Limited mount, dependency, OOM, and namespace snapshots mark decisive data uncollected; namespace also lacks a kernel-capability tool. |
| Queryable evidence, wrong query | Incidents 02, 04, 06, 07, and 08 made at least one invalid mount/cgroup locator call. Incident 02 recovered through discovery. |
| Evidence obtained, interpretation overreach | Incident 01 inferred “never ready” from unobserved dependency state; 07 called 137 an OOM signature; 08 over-excluded security policy. |
| Output format/reference failure | Incident 03 returned prose instead of JSON; incident 06 mistyped one evidence ID. |
| Budget exhausted | None. |
| Provider/executor error | None. |

The strongest common improvement candidate is **entity locator/discovery consistency**, especially
for cgroups. Five of eight runs wasted a call on a name/path/reference mismatch, and four used
`inspect_cgroup_state` with an identifier the fixture could not resolve. A future change should first
evaluate one consistent discovered typed reference or deterministic name/path resolution rather than
adding diagnosis rules. This report does not implement that change.

## Trace index

Each single-line JSON trace contains the model-visible prompt, hashes/config, per-turn usage and
latency, complete tool arguments and redacted results, run-local EvidenceItem registry/provenance,
redacted raw final, parsed final when available, validation issues, and stop reason.

- `experiments/container-startup-paired-eval/traces/startup-eval-01.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-02.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-03.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-04.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-05.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-06.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-07.jsonl`
- `experiments/container-startup-paired-eval/traces/startup-eval-08.jsonl`

## Interview summary

“I evaluated four paired container-startup mechanisms with complete and deliberately missing
evidence. Six of eight runs produced reference-valid finals, but manual audit mattered: one rejected
dependency result was semantically good but copied one evidence ID incorrectly, while an accepted
namespace result over-excluded policy causes. The full mount run actively confirmed mismatched mount
identities; the full OOM and namespace mechanisms were mainly visible in the initial logs. Limited
cases showed whether the model admitted missing evidence rather than guessed. The most repeatable
engineering weakness was not another diagnosis rule—it was inconsistent entity locator semantics,
which caused invalid state queries in five runs.”

This is a four-pair, one-run-per-input evaluation over synthetic or publicly adapted replay data. It
cannot support a general accuracy percentage, a production capability claim, or a statement that
the system diagnoses kernel defects.
