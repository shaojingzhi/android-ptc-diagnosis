# Android Container Runtime Diagnosis

A small, evaluation-first prototype for **Android container runtime diagnosis**:
failure attribution across the container-runtime boundary using normalized Logcat
and KMessage evidence.

## Problem

Current troubleshooting often looks like:

`Logcat + KMessage -> raw logs to LLM -> manual follow-up`

The target engineering problem is not diagnosing arbitrary Android Framework or
application business bugs. It is distinguishing, around the container-runtime
boundary:

- the **primary failure candidate** at the runtime/container boundary,
- **cascading errors** caused by that failure,
- and unrelated / historical **noise**.

This project keeps the scope deliberately narrow: use real ADB where possible, replay cases for reproducible evaluation, and compare RAW / FIXED / DIRECT / PTC / HYBRID execution without building another general agent framework.

## Frozen MVP scope

Input:

- Logcat
- KMessage / kernel-like logs when available
- target package / symptom
- optional failure timestamp

Output:

- Top-K primary failure candidates
- candidate propagation chain
- supporting evidence
- compressed duplicate clusters

The project does not claim to diagnose arbitrary Android Framework internals or
application business logic. Binder failures are treated as runtime/service-health
signals and cascading evidence; this project does not diagnose Binder's internal
implementation.

Not in scope:

- full Android diagnostic platform
- automatic remediation
- generic MCP server
- custom sandbox
- multi-agent / memory / RAG / UI
- rigorous causal inference

## Architecture

```text
Android Emulator / Device              Replay cases
          |                                 |
          +---------- DeviceBackend --------+
                         |
                   normalized events
                         |
                fixed / PTC triage
                         |
              candidate evidence set
                         |
                    LLM reasoning
                         |
          primary candidate + chain
                         |
                     evaluation
```

### External infrastructure

- `Android-Debug-Bridge-MCP`: real Android/ADB tool infrastructure (integration target)
- `open-ptc-agent`: PTC runtime/backend (integration target)
- `Drain3`: log template clustering / duplicate compression

The project should **adapt** these systems, not fork and rebuild them.

### Execution modes

- **RAW**: raw Logcat/KMessage directly to the LLM.
- **FIXED**: deterministic Python triage before LLM reasoning.
- **DIRECT**: traditional per-round LLM tool calling.
- **PTC**: one generated program composes bounded log-processing operations.
- **HYBRID**: PTC first, with a bounded Direct follow-up when evidence is insufficient.

## Milestones

### M0 — scope + replay + interface
- Freeze schemas and boundaries.
- Three original seed cases plus deterministic runtime-boundary replay cases.
- `DeviceBackend` interface.
- Replay backend works.

### M1 — real ADB end-to-end
- Android Emulator / device reachable.
- Collect logcat and basic package/process state.
- One deliberate Java crash case works end-to-end.

### M2 — fixed triage baseline
- normalize timestamps and fields
- Drain3 clustering / duplicate compression
- simple temporal/process/component ranking

### M3 — PTC
- plug in an existing PTC runtime
- same tools, same cases, same model
- compare fixed vs PTC

### M4 — hybrid
- PTC triage first
- at most one direct evidence-acquisition round when evidence is insufficient
- re-triage and final diagnosis

## Evaluation

Keep the first version small:

- Root Cause Top-1
- Root Cause Top-3
- Evidence Recall
- Noise Reduction Ratio
- Context Tokens
- Model Turns
- Latency

Run the deterministic M2 replay baseline with:

```bash
PYTHONPATH=src python scripts/eval_fixed.py
```

It reports Top-1/Top-3 primary-event hits and the reduction from raw events to
one representative per Drain3 template. Event counts are not token counts.

`java_crash_001` is the deliberate Java-crash fixture used by the M1 real-ADB E2E
smoke test. It verifies the Android Emulator/device -> `adb-agent` -> parser path;
it is not a claim of general Android app diagnosis.

Public, hash-pinned Android Logcat fixtures live under `eval/public_logcat/`, outside
the frozen replay benchmark. They validate parser, grouping, compact-evidence, and
provenance behavior on records captured from real devices; they do not supply
root-cause ground truth. See
[`docs/public-android-logcat-validation.md`](docs/public-android-logcat-validation.md).

Public redroid and Waydroid incidents under `eval/public_android_container_incidents/`
exercise real Android-container Logcat. The redroid case separates a fatal Android event
from captured container state; the Waydroid case adds an upstream-confirmed hwcomposer
failure mechanism and source fix. Neither is a local reproduction. See
[`docs/public-android-container-validation.md`](docs/public-android-container-validation.md)
and [`docs/waydroid-hwcomposer-validation.md`](docs/waydroid-hwcomposer-validation.md).
The RAW-vs-registry comparison is documented in
[`docs/waydroid-raw-vs-registry-experiment.md`](docs/waydroid-raw-vs-registry-experiment.md).

`binder_flood_001` is a deterministic synthetic stress case for repeated
downstream Binder and service-unavailable errors. Binder is used as a
runtime/service-health signal, not as an attempt to diagnose Binder internals.

`runtime_mount_failure_001` is a hand-authored deterministic synthetic case for
container-runtime boundary attribution. Its chain is:

```text
KMessage containerfs mount failure
  -> ContainerRuntime startup failure
  -> runtime service unavailable
  -> repeated Binder transaction failures
  -> app-visible startup failure
```

It contains both KMessage and Logcat events, and uses no Huawei or other
production data. Its listed primary candidate, supporting evidence, and
propagation candidates are evaluation labels; they are not a claim of proven
root cause or strict cross-source causality.

### M3a DIRECT smoke

The bounded DIRECT baseline uses the same normalized events and deterministic
diagnostic primitives as the replay pipeline. It is opt-in because it calls a
real model provider:

```bash
pip install -e ".[direct]"
OPENAI_API_KEY=... python scripts/eval_direct.py --trace-file /tmp/direct.jsonl
```

Set `DIRECT_MODEL` (and optionally `DIRECT_BASE_URL`) to select the provider
configuration. The script does not run in normal CI and does not store API keys
in the repository.

## Design rule

> Add code only when it improves real executability, evaluation credibility, or interview explainability.
