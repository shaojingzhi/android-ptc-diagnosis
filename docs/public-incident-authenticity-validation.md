# Public incident authenticity validation

This validation asks a deliberately narrower question than model diagnosis: can the existing replay
pipeline ingest a small, attributed excerpt from a real upstream container incident without silently
rewriting its evidence? It does **not** claim local reproduction, production provenance, or diagnosis
accuracy.

## Inputs and provenance

Three neutral replay inputs were created from short operational excerpts in upstream issue reports.
The model-visible copy is byte-identical to `source_excerpt.txt`; `source.yaml` records its SHA-256,
the source URL, versions reported by the author, facts supported by the source, and claims that remain
unsupported. Hidden `expected.yaml` files are not read by `ReplayBackend`.

They live under `eval/public_incidents/`, outside the frozen `eval/cases/` seed benchmark. This keeps
the historical seed-case assumptions and metrics unchanged while still exercising the same case
loader, parser, clustering, compact, and primitive code.

| Case | Upstream material | What is preserved | Important boundary |
| --- | --- | --- | --- |
| `public-incident-01` | [containerd #10228](https://github.com/containerd/containerd/issues/10228), resolved by [PR #10274](https://github.com/containerd/containerd/pull/10274) | One complete containerd `StartContainer` error with overlay options and `invalid argument` | Reporter-provided issue log; not locally reproduced, and no kernel/mountinfo evidence is present |
| `public-incident-02` | [runc #4427](https://github.com/opencontainers/runc/issues/4427) | Five selected kernel records containing PID, cgroup path, 15 MiB limit, `CONSTRAINT_MEMCG`, and the killed process | Real Kubernetes CI issue excerpt, not the complete CI artifact; no before/after `memory.events` sample |
| `public-incident-03` | [containerd #10846](https://github.com/containerd/containerd/issues/10846) | The reported containerd-to-Kata-shim socket error and `connection refused` | The issue author's Go-version explanation is context, not independently reproduced evidence |

The copied records remain attributed short excerpts. The upstream repository license is not assumed
to license issue prose. No username, credential, private path, or project-owned production data was
added. Public hostnames, PIDs, cgroup paths, container IDs, and snapshot IDs are retained because they
are part of the cited public evidence.

## Deterministic results

| Case | Normalized events | Compact groups | Parsed wall time/tag/level | Key evidence reachable | FIXED behavior |
| --- | ---: | ---: | --- | --- | --- |
| containerd overlay | 1 Logcat-source event | 1 | 0 / 0 / 0 | yes, event 0 | event 0 is trivially Top-1 at score 0.10 |
| runc memcg | 5 KMessage-source events | 5 | 0 / 0 / 0 | yes, kill record event 4 | event 4 ranks fifth, outside Top-3 |
| containerd shim | 1 Logcat-source event | 1 | 0 / 0 / 0 | yes, event 0 | event 0 is trivially Top-1 at score 0.10 |

All seven non-empty source lines survive byte-for-byte as normalized `message` values. Their source
line numbers survive in `LogEvent.raw_line_number`; every normalized event belongs to exactly one
compact group; global event indices remain usable through `filter_by_text()` and `get_events()`.
Source-ledger hashes, exact model-visible copies, parser behavior, evidence reachability, compact
membership, neutral incident IDs, and the absence of invented runtime state are covered by offline
tests.

## What the test exposed

The tolerant fallback prevents data loss, but it does not parse journald/containerd prefixes. The
embedded timestamps, severity, process identity, and component remain inside the message string, so
the normalized structured fields are `None`. This is why the decisive OOM kill record receives only
the generic unknown-severity/first-seen FIXED signals and ranks behind earlier context lines. The
single-record cases appearing Top-1 is not meaningful ranking evidence.

The original `LogEvent` retains `raw_line_number`, while the current `get_events()` result exposes the
global normalized `index` rather than the source line number. The two remain correlatable inside the
pipeline, but a model-visible source-line reference would require a deliberate primitive-contract
change and was not added here.

No `runtime_state.yaml` was invented. Consequently, the public cases validate real-text ingestion and
evidence preservation only; process, mount, listener, and cgroup follow-up state that the upstream
reports did not provide remains unavailable. This is a data gap, not an empty observation.

## Conclusion

The current pipeline can preserve and compact selected public Linux-container incident records, but
its normalization and FIXED scoring remain Android-Logcat-centric. Before treating a larger Linux
corpus as a comparable diagnosis benchmark, the smallest justified follow-up would be a thin,
source-explicit journald/syslog parser adapter. Reformatting upstream records as fabricated Logcat
would make the benchmark look cleaner while weakening its provenance, so this validation intentionally
does not do that. No model or paid API was called.
