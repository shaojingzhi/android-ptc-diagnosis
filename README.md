# Android PTC Diagnosis

A small, evaluation-first prototype for **causal log triage** in Android/container diagnostics.

## Problem

Current troubleshooting often looks like:

`Logcat + KMessage -> raw logs to LLM -> manual follow-up`

The hard part is not finding `ERROR` lines. It is distinguishing:

- the **primary failure candidate** that first affected the app,
- **cascading errors** caused by that failure,
- and unrelated / historical **noise**.

This project keeps the scope deliberately narrow: use real ADB where possible, replay cases for reproducible evaluation, and compare RAW / FIXED / DIRECT / PTC / HYBRID execution without building another general agent framework.

## Frozen MVP scope

Input:

- Logcat
- optional KMessage / kernel-like logs
- target package / symptom
- optional failure timestamp

Output:

- Top-K primary failure candidates
- candidate propagation chain
- supporting evidence
- compressed noise clusters

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
- Three seed cases.
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

## Design rule

> Add code only when it improves real executability, evaluation credibility, or interview explainability.
