# Real-log Compact-DIRECT cost attribution

This report analyzes the single frozen real-log Compact-DIRECT smoke produced by
commit `53fb8a0` and documented by commit `32de2dc`. It is an offline byte-level
analysis only: no model or Daytona call was made, no prompt was changed, and the
frozen result was not rerun.

## Reproduction boundary

The analysis used the reviewed, sanitized 545-event JSONL snapshot, capture
metadata, and frozen JSONL trace kept outside the repository. The task was
deterministically reconstructed with the exact request and explicit baseline /
incident indices used by the smoke. It reproduced all frozen input facts:

- 545 normalized events and 275 compact groups;
- 14 baseline, 371 incident, and 160 unassigned events;
- 122,654 UTF-8 bytes in the model task;
- 113,169 bytes in the compact model view when serialized with the trace's
  compact JSON separators.

The frozen trace SHA256 remains
`3eab12983a4dd4243eb58749cc8d56eeb2858a3b602b68213e9e54fd24da5d46`.

## Model-task byte attribution

The table below is an exact, additive decomposition of the 122,654-byte task.
It was computed by blanking template values, removing incident-comparison
fields, and replacing group objects with empty objects in fixed order. As a
result, the categories sum exactly, but JSON punctuation adjacent to a removed
field is assigned according to that order.

| Category | Bytes | Share |
|---|---:|---:|
| Fixed instructions and separating newline | 461 | 0.38% |
| Template text values | 35,819 | 29.20% |
| Incident/baseline fields | 29,042 | 23.68% |
| Remaining per-group metadata | 55,770 | 45.47% |
| Other payload fields and JSON structure | 1,562 | 1.27% |
| **Total** | **122,654** | **100.00%** |

“Incident/baseline fields” includes the four per-group comparison fields and
the top-level incident context. “Other” includes the symptom, target package,
failure timestamp, counts, output schema, wrapper objects, and the residual
list/object structure. The result disproves the idea that the fixed instruction
paragraph is the main cost. Repeating rich metadata for all 275 groups is the
largest contributor.

## Group distribution and dominant fields

With the same default JSON serialization used in the task, the 275 individual
group objects occupy 120,821 bytes; their enclosing list occupies 121,371
bytes. Group object sizes are:

| Statistic | Bytes |
|---|---:|
| Minimum | 306 |
| P25 | 345 |
| Median | 376 |
| P75 | 421 |
| P90 | 532 |
| P95 | 949 |
| P99 | 1,717 |
| Maximum | 2,204 |
| Mean | 439 |

The distribution has a long tail, but the cost is not caused by one outlier:
the largest group is only 1.82% of individual-group bytes; the largest 10 are
11.55%, the largest 25 are 21.06%, and the largest 100 are 48.73%. There are
199 singleton groups. The other 76 groups represent 346 events, so Drain3 does
compress repetition while leaving a high-cardinality real-log catalog.

An exact second view decomposed the serialized group list into standalone field
fragments plus 8,800 bytes of list/object/separator structure. Its largest
contributors were:

| Group field or structure | Bytes | Share of group list |
|---|---:|---:|
| `template` field | 39,669 | 32.68% |
| `not_seen_in_observed_baseline` | 10,249 | 8.44% |
| JSON list/object/separator structure | 8,800 | 7.25% |
| `representative_index` | 7,359 | 6.06% |
| `tag` | 6,108 | 5.03% |
| `unassigned_count` | 5,777 | 4.76% |
| `template_id` | 5,385 | 4.44% |
| `incident_count` | 5,230 | 4.31% |
| `baseline_count` | 5,226 | 4.31% |

The remaining fields are individually smaller but collectively substantial.
For example, `source`, three index anchors, `group_id`, `level`, `pid`, and
`count` are repeated for every group.

Plain template values total 35,811 bytes (median 67, P90 222, P95 631, maximum
1,899). The four largest templates are WindowManager transition metadata
(1,899 bytes), WindowManagerShell transition metadata (1,870), SearchTargetUtil
result metadata containing an embedded bitmap representation (1,486), and a
TransactionExecutor activity-launch record (1,374). Across 14 groups,
SearchTargetUtil alone contributes 9,232 template-text bytes; WindowManager and
WindowManagerShell together contribute 7,390. These are real background/UI
records, not evidence selected from ground truth.

The phase distribution also explains the repeated comparison cost: 201 groups
are incident-only, 69 unassigned-only, one baseline-only, and four occur in
both baseline and incident. A long boolean key plus three integer counters is
serialized for every group even when most values are zero.

## What the token trace can and cannot prove

The trace records the following actual aggregate usage:

- 3 model turns and 2 tool-calling rounds;
- 139,826 input tokens, 1,027 output tokens, 140,853 total tokens;
- four indexed `get_events` calls and no full-log read;
- tool-result payloads of 7,861, 4,308, 4,577, and 4,661 bytes (21,407 bytes
  total).

It does **not** contain per-turn token usage. Therefore no actual turn-by-turn
token attribution is possible from this trace. Dividing the aggregate evenly,
converting bytes to an assumed token ratio, or treating tool-result bytes as
tokens would fabricate measurements. It is plausible that the large initial
task and later retransmission of accumulated context dominate input usage, but
that remains a hypothesis until a future trace records provider usage for each
turn. The 21,407 tool-result bytes are known wire content, not a measured token
contribution.

## Candidate two-level evidence index

The smallest candidate design is a presentation change, not a new diagnosis
engine:

1. **Level 1: bounded detailed evidence.** Include incident context and a
   deterministic union of groups selected by generic signals: occurrence in
   the explicit incident interval, change relative to the observed baseline,
   lexical overlap with request fields such as target package/component and
   symptom, proximity to recorded incident boundaries, and diversity quotas
   across source/tag/level. No crash phrase, ground-truth index, Android-specific
   exception rule, or severity-only filter is allowed.
2. **Level 2: deferred group index.** Keep all other groups in a deterministic
   server-side index keyed by stable `group_id`. Initially expose only aggregate
   facet counts and the number of deferred groups. Add one bounded read-only
   query over the existing group data (filters such as group IDs, text, tag,
   source, or phase, with a result limit) so the model can request full template
   metadata when its current hypothesis needs it.
3. **Raw evidence remains authoritative.** All 545 normalized events, raw line
   provenance, group membership, baseline/incident/unassigned assignment, and
   indexed `get_events` access remain unchanged. Level 1 is an index, not a
   destructive filter.

This design keeps diagnosis choices with the model while moving deterministic
catalog mechanics out of the initial context. It also avoids sending a smaller
but still repetitive 275-row “lightweight catalog” that could preserve most of
the current metadata cost.

### Preventing silent omission

The bounded first level cannot be accepted merely because it is smaller. Before
any model experiment, offline checks should require:

- every raw event belongs to exactly one deferred-or-detailed group and remains
  reachable by its original normalized index;
- known evidence indices in existing fixtures remain reachable, and their
  groups appear either in Level 1 or in deterministic Level-2 query results;
- Level 1 reports its selection reasons and coverage by source, tag, level, and
  phase, including non-ERROR representatives;
- target-related and incident-boundary context is always included with bounded
  neighbors;
- empty/low-confidence selection triggers a deterministic breadth expansion,
  not a severity-only fallback;
- selection limits, ordering, and query results are stable across repeated
  offline runs.

Ground truth may be used to measure recall after selection, but never as an
input to selection. Evaluation should include held-out locally reproduced logs
before claiming that the index preserves diagnosis quality.

## Expected implementation surface and offline metrics

No implementation is made in this report. A future minimal change would likely
touch only:

- `compact.py`: construct detailed and deferred views from the existing compact
  groups while preserving group identity and provenance;
- `execution.py`: opt Compact-DIRECT into the two-level request shape without
  changing the frozen path by default;
- `primitives.py`: add or extend one bounded read-only group query rather than
  introducing a new retrieval framework;
- the real-log evaluation script and focused tests: report size, coverage, and
  deterministic query behavior.

Offline acceptance metrics should include initial task bytes and byte reduction,
Level-1/deferred group counts, per-field bytes, source/tag/level/phase coverage,
ground-truth evidence reachability, raw-event provenance coverage (target
100%), maximum query result size, and deterministic repeatability. Token and
accuracy savings are explicitly **not yet validated**; they require a separately
approved model run after the offline representation passes these checks.

## Conclusions

Confirmed: the 122,654-byte initial task is dominated by repeated group
metadata, template values, and comparison fields; the fixed instructions are
negligible. Confirmed: the frozen trace has aggregate but not per-turn token
usage. Confirmed: indexed tool outputs add 21,407 bytes while preserving bounded
raw access.

Not confirmed: how much of the 140,853 aggregate tokens came from the first
task, from context retransmission, or from tool results; whether a two-level
index reduces model tokens proportionally; and whether it preserves real-log
diagnostic accuracy. Those remain testable hypotheses, not benchmark results.
