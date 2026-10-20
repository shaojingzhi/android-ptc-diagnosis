# RAW vs Registry-backed diagnosis: Waydroid #1894

## Executive summary

This report records one controlled comparison on a public Android-container Logcat incident. The question
was whether the registry-backed compact/tool workflow produced a better diagnosis than giving the same raw
Logcat directly to DeepSeek.

The result does **not** show that the tool workflow is more accurate. Both runs identified the same
hwcomposer native null-pointer failure. RAW used substantially fewer tokens and less latency, but its
final violated the output schema. The registry-backed run had valid EvidenceItem references and an
accepted final, but it read the complete event set before using the higher-level context primitive and
made several statements that were stronger than the supplied evidence justified.

The appropriate conclusion is limited:

> This high-signal case demonstrates that RAW can already diagnose the primary failure. The registry path
> adds provenance, bounded-query capabilities, and reference auditing, but this experiment did not show a
> diagnosis-quality advantage or effective bounded retrieval.

## Experimental setup

Both runs used the same:

- public Waydroid issue #1894 excerpt;
- exact contiguous Logcat lines 5,148–5,304;
- 157 normalized events and 73 compact groups;
- neutral incident ID and symptom;
- `deepseek-v4-pro`, temperature 0, thinking disabled;
- provider retries disabled;
- no issue discussion, upstream fix commit, assessment, or hidden expected answer;
- one run per condition, no retry and no prompt tuning.

The independent source of mechanism confirmation is the Waydroid issue and the upstream
[`3b2f29d`](https://github.com/waydroid/android_hardware_waydroid/commit/3b2f29d0b41b89fef5c34ed054be589e58e3c7cb)
fix. That source was used only for post-run audit.

## Conditions

### RAW

The model received the raw Logcat records with a deterministic normalized-event index prefix. It had no
diagnostic tools, no compact groups, and no EvidenceItem registry. The text after each prefix was unchanged.

### Registry-backed Compact-DIRECT

The model received the existing compact evidence and could use the current typed evidence tools and
EvidenceItem registry. It was free to choose tools; no tool call was forced.

## Metrics

| Metric | RAW | Registry-backed Compact-DIRECT |
|---|---:|---:|
| final status | rejected: schema | accepted |
| model turns | 1 | 4 |
| tool calls | 0 | 6 |
| tool rounds | 0 | 3 |
| input tokens | 10,244 | 120,794 |
| output tokens | 642 | 2,901 |
| total tokens | 10,886 | 123,695 |
| latency | 10.3 s | 33.3 s |
| final validation errors | `notes` string instead of list | none |

RAW used approximately 91.2% fewer total tokens and 69.1% less latency. This is not a general efficiency
claim: the comparison is between one no-tool invocation and a multi-turn tool workflow. The task sizes
were similar; the registry run's cost came primarily from repeated tool/result context.

## Semantic comparison

### Primary failure

Both outputs localized the incident to the same failure site:

- `vendor.hwcomposer-2-1`;
- SIGSEGV / null pointer dereference;
- `wl_proxy_marshal` followed by `hwc_set` in `hwcomposer.waydroid.so`.

The RAW output cited event indices `[36, 37, 44]`. The registry output cited EvidenceItems resolving to
the corresponding event records. Neither output named the exact `display->pointer` field; that information
was not present in the model-visible input.

### Propagation and noise

Both runs treated SurfaceFlinger `DEAD_OBJECT`, SIGABRT, service-registration failures, and later surface
errors as downstream or related observations. Both avoided selecting the earlier cgroup
`Read-only file system` messages as the primary failure, even though the deterministic FIXED baseline
ranks one of those messages Top-1.

The RAW output was more explicit about uncertainty: it kept open whether cgroup errors were independent,
whether Binder ordering was causal, and whether service-registration failures were causes or effects.
The registry output was accepted structurally but overclaimed global “earliest” ordering and the scope and
persistence of the cgroup problem.

### Recommended checks

RAW suggested source/symbol inspection, Wayland socket/display checks, cgroup status verification, a full
earlier boot log, and an image-version comparison. These are diagnostic checks. The registry output also
suggested “correct it to rw,” which mixes remediation into a read-only diagnostic recommendation.

## Evidence and engineering comparison

| Property | RAW | Registry-backed workflow |
|---|---|---|
| raw signal access | direct | through compact evidence and tools |
| EvidenceItem registry | absent | present |
| reference validation | manual event-index audit | deterministic registry validation |
| provenance | raw line/index context | event and tool-return provenance |
| follow-up evidence acquisition | unavailable | available in principle |
| actual bounded behavior here | all evidence visible at once | four indexed reads covered all 157 events before context query |
| accepted structured final | no | yes |

The registry path therefore demonstrated better auditability, not better diagnosis on this case. Its
higher-level `inspect_failure_context` primitive was selected, but only after the model had already read
the full event set. This is a concrete planning/cost badcase rather than evidence that the primitive is
useless in general.

## What this experiment does and does not establish

It establishes:

- the public incident is high-signal enough for a raw model to localize the native failure;
- the structured path can preserve and validate evidence references;
- severity and early occurrence alone can misrank a real incident;
- tool access can add substantial context cost without automatically improving the answer.

It does not establish:

- general diagnosis accuracy;
- superiority of RAW, Compact-DIRECT, PTC, or registry-backed execution;
- stable token or latency ratios;
- usefulness of bounded queries on low-signal or distributed incidents;
- production readiness.

The RAW final's schema rejection is retained as observed. It was not repaired or rerun, so semantic
quality and format validity remain separate conclusions.

## Reproducibility artifacts

- [RAW trace](../experiments/waydroid-hwcomposer-raw-ablation/trace.jsonl)
- [RAW audit](../experiments/waydroid-hwcomposer-raw-ablation/audit.md)
- [Registry trace](../experiments/waydroid-hwcomposer-registry-smoke/trace.jsonl)
- [Registry audit](../experiments/waydroid-hwcomposer-registry-smoke/audit.md)
- [Waydroid case description](waydroid-hwcomposer-validation.md)

No additional model call was made while preparing this report.
