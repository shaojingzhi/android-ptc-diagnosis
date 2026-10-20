# Public Android-container registry smoke

This was one controlled DeepSeek run on the public redroid issue #623 artifacts. It tested whether
the existing registry-backed Compact-DIRECT workflow could keep authentic Logcat, Docker inspect,
and process-list observations separately attributable. It was not a benchmark, a locally reproduced
incident, or a root-cause accuracy measurement.

## Frozen configuration

- implementation commit: `54d35ab4cc039da34c5e30f656439fbe3048f650`
- neutral incident ID: `incident-public-android-container-01`
- model: `deepseek-v4-pro`, temperature 0, thinking disabled, provider retries 0
- maximum tool-calling rounds: 6
- input: 75 normalized real Logcat events in 45 compact groups
- initial captured state: Docker `State` plus all 82 process-snapshot rows
- model-visible tools: the nine existing log evidence tools; no replay runtime-state tools
- task/system/tool hashes and all three input hashes: saved in `trace.jsonl`

The Docker and process observations were registered as three system-owned EvidenceItems under one
captured container subject. Their provenance points to neutral artifact IDs and exact top-level field
paths. The model was explicitly told that the snapshots have no encoded capture time and must not be
strictly ordered against Logcat.

## Execution result

| Metric | Result |
| --- | ---: |
| Final status | accepted |
| Model turns | 5 |
| Tool-calling rounds | 4 |
| Tool calls | 6 |
| `get_events` records returned | 71 (explicit indexed reads; no full read) |
| Input tokens | 112,000 |
| Output tokens | 2,482 |
| Total tokens | 114,482 |
| Total latency | 37,859 ms |

The model made five indexed `get_events` calls and one `filter_by_time` call. It did not use the
higher-level `inspect_failure_context` primitive. The accepted final cited only run-local handles;
all resolved to registered evidence. The trace retains the redacted raw final, parsed and resolved
payloads, tool arguments/results, registry, provenance, and per-turn usage.

## Manual semantic audit

Format/reference acceptance and diagnosis quality are separate. The former passed; the latter was
mixed:

1. The system_server SIGSEGV, PID, `memtrack_proc_new`, and stack path were supported. Calling the
   zero fault address a proven null-pointer dereference was stronger than the captured record.
2. Event 0 was the first fatal record in this bounded crash buffer. Calling it the likely trigger of
   the initial connection failure remained a hypothesis because the failed-connection time and
   pre-buffer logs were absent; the final correctly listed that gap.
3. Launcher/SystemUI `DeadSystemException` records were reasonably treated as propagation evidence,
   rather than independent primary candidates.
4. The separate audio-service SIGABRT and destructor stack were supported. The final appropriately
   kept its relationship to system_server uncertain.
5. The Docker snapshot really reported `running`, with no restart/OOM flags, and the process snapshot
   really contained replacement system_server/audio processes. However, “subsequently recovered”
   inferred an ordering that the artifacts do not encode. A later evidence-gap entry acknowledged
   the missing timestamps, but did not repair the overclaim in the claim itself.
6. The final's “known class of failure” and null memtrack handle explanation came from unsupported
   model knowledge, not the registered evidence. It should remain an unverified investigation
   hypothesis, not an evidence-backed incident conclusion.

The recommended checks were concrete and diagnostic: collect complete startup Logcat, correlate the
connection-attempt time, inspect memtrack registration/implementation, attempt a bounded
reproduction, and investigate the audio crash separately. No automatic remediation was proposed.

## Conclusion and limitation

The workflow successfully made authentic multi-artifact evidence citable and auditable, and the
model used both captured-state handles in its final. It also exposed why deterministic reference
validity is not semantic entailment: an accepted answer still introduced one unsupported mechanism
and one false cross-artifact ordering. This run is therefore useful as a case demonstration of the
copilot and its audit boundary, not evidence of stable diagnosis quality or a proven root cause.
