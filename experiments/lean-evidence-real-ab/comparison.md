# Lean evidence presentation: exploratory real-model A/B

## Scope and frozen configuration

This experiment ran `startup_dependency_unready_full` exactly once per arm, exposed to the model as
`incident-evidence-ab-01`. Both executions used repository commit
`9c28ed7958e7c7ae17f6dcd70159a478d8b77013`, DeepSeek `deepseek-v4-pro`, temperature 0, thinking
disabled, six diagnostic tool rounds, and provider retries disabled. System prompt, diagnosis schema,
tool schema, case inputs, incident ID, and executor code were identical. The intended variable was
the model-visible evidence representation and its minimal format instruction.

The expected case file was used only after both calls for manual evaluation. It was not present in
either task prompt. The two runs are exploratory samples, not a reliability or statistical estimate.

## End-to-end provider metrics

| Metric | Hardened baseline | Lean | Interpretation |
| --- | ---: | ---: | --- |
| final status | accepted | accepted | Both passed JSON, schema, and reference validation. |
| input tokens | 99,724 | 20,304 | Lean was 79.64% lower in these two runs. |
| output tokens | 2,016 | 1,567 | Lean was 22.27% lower. |
| total tokens | 101,740 | 21,871 | Lean was 78.50% lower. |
| latency | 26,381.83 ms | 17,874.16 ms | Lean was 32.25% lower. |
| model turns | 7 | 4 | Investigation paths differed. |
| tool-calling rounds | 6 | 3 | Baseline used the full six-round budget; Lean did not. |
| tool calls | 10 | 8 | Lean omitted mount/cgroup exploration. |
| invalid tool calls | 1 | 0 | Baseline guessed an unknown cgroup reference. |
| finalization fallback | false | false | Real-provider fallback behavior was not exercised. |
| registry items | 56 | 52 | Different queries surfaced different evidence. |
| unique referenced handles | 21 | 25 | Lean used more unique evidence references despite fewer calls. |
| invalid references | 0 | 0 | Every emitted handle resolved exactly. |
| main failure candidate | HostBridge initialization/registration stalled | HostBridge registration/listener readiness stalled | Core attribution was consistent. |
| key evidence | ECONNREFUSED; HostBridge starting/not ready/listener false/loading; processes alive; runtime dependency pending | Same core logs and queried process/service fields | Both used actual surfaced evidence. |
| critical gaps | Why registration/loading and bind failed; socket state | Why registration/loading and bind failed; transient versus persistent refusal | Both preserved the unknown internal mechanism. |
| unsupported causal language | “never”; high-confidence “because”; Thermal warning declared unrelated | “never”; causal statements typed as observations; Thermal warning declared unrelated | Both overstate beyond the observation window. |

The percentage calculations use these two new traces. The historical 79,300-token smoke is not the
baseline for this table.

## Per-turn provider usage

### Hardened baseline

| Turn | Input | Output | Cumulative input | Tools | Latency ms |
| ---: | ---: | ---: | ---: | --- | ---: |
| 1 | 4,354 | 180 | 4,354 | `get_events`, `inspect_failure_context` | 2,679.16 |
| 2 | 11,343 | 107 | 15,697 | process discovery, service discovery | 2,132.52 |
| 3 | 13,743 | 236 | 29,440 | HostBridge service/process and runtime service inspection | 2,538.54 |
| 4 | 16,688 | 150 | 46,128 | mount discovery | 2,502.22 |
| 5 | 17,372 | 259 | 63,500 | invalid cgroup inspection | 3,140.21 |
| 6 | 17,659 | 105 | 81,159 | runtime-init process inspection | 2,170.81 |
| 7 | 18,565 | 979 | 99,724 | final response | 11,209.17 |

### Lean

| Turn | Input | Output | Cumulative input | Tools | Latency ms |
| ---: | ---: | ---: | ---: | --- | ---: |
| 1 | 2,754 | 162 | 2,754 | `get_events`, process discovery, service discovery | 2,144.63 |
| 2 | 4,734 | 280 | 7,488 | two process and two service inspections | 2,680.14 |
| 3 | 5,487 | 298 | 12,975 | `inspect_failure_context` | 3,513.00 |
| 4 | 7,329 | 827 | 20,304 | final response | 9,523.45 |

The first-turn provider input fell from 4,354 to 2,754 tokens before either arm had tool history,
which is direct evidence that the smaller initial representation reduced provider input for this
sample. Later and aggregate differences cannot be attributed to serialization alone because the
model selected different tools and used three fewer turns.

## Handle, path, and provenance audit

The baseline final made 23 references to 21 unique handles. The Lean final made 29 references to 25
unique handles. Both handle maps reconstructed deterministically from the frozen replay and tool
traces, all referenced handles had actually been supplied, and canonical resolution matched the
resolved final payload exactly. No unknown handle was repaired or ignored.

For Lean, all 43 tool-result handle/path mappings resolved to a real value in the corresponding tool
result. The final cited both inline compact handles and path-mapped raw/runtime observations,
including the HostBridge state, readiness, listener, registration state, runtime dependency, endpoint,
and connection result. All 52 registry items retained provenance. This demonstrates basic real-model
usability of the path representation in this run; it does not establish stable interpretation.

## Independent semantic review

### Hardened baseline

The baseline correctly reconstructed the observable chain: the runtime stayed alive, depended on
HostBridge, received `ECONNREFUSED`, remained pending, and eventually timed out while HostBridge was
starting, not ready, not listening, and still loading registration. It queried both services and both
processes and correctly retained the absence of evidence explaining why HostBridge initialization
stalled. Its next checks were concrete and read-only.

The answer nevertheless used “never” beyond the finite observation window, expressed the dependency
chain with high causal confidence, and classified the ThermalMonitor warning as unrelated based
mainly on lack of a link. Its invalid cgroup lookup returned no evidence and was not cited. It did not
invent cgroup or host-memory findings.

### Lean

The Lean answer preserved the same principal diagnosis and used actual path-mapped service/process
state as well as log evidence. It explicitly retained why registration stayed loading and whether
`ECONNREFUSED` was transient as open questions. Its next checks remained read-only and useful, and it
did not invent cgroup or host-memory state.

Its key weakness is causal phrasing inside high-confidence `observation` claims: the connection failed
“because” the listener was not bound, and the timeout was a “downstream consequence.” Those are
reasonable hypotheses but stronger than deterministic observation. It also used “never” and declared
the ThermalMonitor warning unrelated without positive exclusion evidence. These issues are similar
to, not clearly worse than, the baseline's overclaims.

## Interpretation

Lean reduced actual provider input tokens by 79.64% and total tokens by 78.50% in this two-run
comparison while preserving accepted references, the main failure candidate, supporting evidence,
important uncertainty, and useful next checks. Diagnosis quality was therefore broadly preserved for
this sample, with qualified causal-language issues in both arms.

The observed end-to-end saving came from both a smaller representation and a changed investigation
path. The fixed-sequence offline analysis isolates a 56.7% byte reduction, and the first model turn
shows a 36.7% provider input-token reduction before tool-path divergence. Lean also used three fewer
turns and two fewer calls, which materially affected the aggregate 79.64% reduction. This experiment
cannot apportion an exact fraction to each cause, establish an average saving, or prove diagnosis
quality stability.
