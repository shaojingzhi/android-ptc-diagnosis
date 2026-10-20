# Waydroid RAW ablation audit

## Frozen setup

- implementation commit: `18b93a7`
- neutral incident ID: `incident-public-android-container-02`
- exact same 157-line Logcat excerpt as the registry-backed run
- no compact groups, registry, diagnostic tools, issue text, fix commit, or hidden assessment
- model: `deepseek-v4-pro`, temperature 0, thinking disabled, provider retries 0
- one model invocation; no retry, prompt adjustment, or output repair

The raw records were supplied with a deterministic normalized-event index prefix so the answer could be
audited against the same event indices. The text after each prefix is unchanged. The raw prompt was 28,084
bytes and the excerpt itself was 23,286 bytes.

## Metrics

| Metric | RAW | Registry-backed Compact-DIRECT |
| --- | ---: | ---: |
| final status | rejected (schema) | accepted |
| model turns | 1 | 4 |
| tool calls | 0 | 6 |
| input tokens | 10,244 | 120,794 |
| output tokens | 642 | 2,901 |
| total tokens | 10,886 | 123,695 |
| latency | 10.3 s | 33.3 s |

RAW used about 91.2% fewer total tokens and 69.1% less latency in this case. This is a comparison of a
single no-tool call with a four-turn tool workflow, not a controlled claim that raw text is generally more
efficient. The task text sizes were similar; the large Compact-DIRECT cost came from repeated tool/result
context, not from the initial compact payload alone.

## RAW semantic audit

Although the final was rejected by the schema validator, the redacted raw payload is preserved and manually
auditable. It selected primary indices `[36, 37, 44]`, covering SIGSEGV/null-pointer evidence and the
`wl_proxy_marshal`/`hwc_set` frames. Its evidence set included the composer tombstone, process death,
SurfaceFlinger `DEAD_OBJECT`, service restarts, and later system-process surface errors. Its propagation
indices connected the graphics failure to SurfaceFlinger and startup-loop records.

The answer explicitly kept uncertainty about:

- the exact trigger for the null dereference;
- whether read-only cgroup errors are a separate configuration issue;
- whether Binder `DEAD_OBJECT` ordering and causality are fully established;
- whether repeated service-registration failures are causes or effects.

Its recommended checks were diagnostic rather than automatic remediation: inspect symbols/source,
display/GPU and Wayland socket state, verify cgroup mount status, obtain an earlier complete boot log,
and compare an updated image. This was more cautious than the registry run's “correct it to rw” suggestion.

The semantic conclusion matches the independent upstream mechanism at the component/failure-site level,
but RAW did not identify the exact missing `display->pointer` field. It did not have access to that source
context.

## Format and evidence-boundary audit

The only validation failure was:

```text
notes: Input should be a valid list; model returned a string
```

No tool was available to validate or repair the answer. Therefore the result is semantically useful but not
an accepted structured diagnosis. RAW also has no EvidenceItem registry or tool provenance, so its event
indices are manually auditable references rather than registry-validated claims.

## Interpretation

This ablation weakens the claim that the agent needs compact/tool evidence to find this particular fault:
the raw Logcat itself contains a very strong native tombstone and the model found it in one call. It does
not eliminate the system's engineering value. The registry-backed path provides deterministic identity,
bounded retrieval, provenance, and a place to audit evidence references; RAW provides none of those safety
properties and cannot perform follow-up evidence acquisition.

The more concerning result is cost: the current tool workflow read all 157 events before using
`inspect_failure_context`, while RAW finished in one call. Future evaluation should therefore separate
three questions: semantic diagnosis on high-signal logs, evidence-boundary/audit guarantees, and whether
bounded retrieval reduces context on cases where the raw log is genuinely too noisy to read directly.
