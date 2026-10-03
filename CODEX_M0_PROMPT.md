# Codex Project Handoff — Android Container Causal Log Triage with PTC

You are taking over a small, job-oriented engineering prototype. Read this entire document before changing code.

The user is an experienced Linux/C++ systems engineer working on an Android-container substrate on HarmonyOS devices. In day-to-day troubleshooting, engineers often collect large Logcat and KMessage/kernel logs, then give those logs directly to an LLM for analysis. The practical pain point is not simply “find ERROR lines”: logs contain many unrelated historical errors and many cascading downstream errors. It is often difficult to identify the earliest event that actually affected application behavior and distinguish it from later symptoms.

This project is intentionally **small and focused**. It is NOT a general Android diagnostic platform and NOT a new Agent framework.

The project exists for two goals:

1. Solve a realistic engineering problem from an Android-container troubleshooting workflow.
2. Produce a project that is easy to explain in interviews and clearly demonstrates the user's own contribution: problem abstraction, PTC/Hybrid execution design, evaluation, and badcase-driven iteration.

## 1. Project thesis

Current workflow:

```text
Engineer collects Logcat + KMessage
        -> raw logs are given to an LLM
        -> LLM analyzes them
        -> engineer manually collects more evidence when needed
```

Target evolution:

```text
Real Android device/emulator via ADB
        -> collect raw evidence
        -> normalize / cluster / deduplicate
        -> PTC-based log triage
        -> a small candidate evidence set
        -> LLM identifies primary-failure candidates and cascading-error chains
        -> if evidence is insufficient, at most one Direct Tool Calling follow-up
        -> final diagnosis + trace + evaluation
```

The project should answer a narrow engineering question:

> In noisy Android/container troubleshooting logs, can Programmatic Tool Calling (PTC) reduce context noise and model round trips while preserving the evidence needed to identify primary-failure candidates, and when is a Hybrid PTC + Direct Tool Calling path better than either approach alone?

Do NOT claim strict causal inference. Use terms such as:

- primary failure candidate
- cascading error
- failure propagation candidate
- supporting evidence
- evidence gap

## 2. Non-negotiable design constraints

The user's highest-level priorities are:

1. **Job search first.** The system should be easy to explain in an interview.
2. **Small and understandable.** Prefer a compact codebase over framework completeness.
3. **Real executability.** At least one path must work against a real Android emulator/device through ADB.
4. **Evaluation credibility.** Replay cases must make Direct / Fixed / PTC / Hybrid comparisons reproducible.
5. **Clear personal contribution.** Reuse commodity infrastructure and focus custom code on the diagnostic workflow, execution policy, trace, and eval.
6. **No architecture inflation.** Do not add abstraction layers merely because they are “cleaner”.

Soft code budget for the user's own core implementation: roughly **1,500–2,500 lines** excluding fixtures/tests/vendor dependencies. This is a guideline, not a target to fill.

Prefer:

- simple functions
- small dataclasses / Pydantic models only where useful
- explicit data flow
- JSONL traces
- a few well-named modules

Avoid:

- multi-agent systems
- memory
- RAG/vector databases
- a web UI
- a workflow engine
- a custom MCP protocol implementation
- a custom generic sandbox
- a generic PTC framework
- a general causal graph engine
- dozens of Android tools
- many layers such as planner/orchestrator/scheduler/policy-manager/context-manager unless a concrete need appears

If a proposed change does not improve one of these three things, do not add it:

- real executability
- evaluation credibility
- interview explainability

## 3. Reuse existing GitHub projects instead of rebuilding them

Before implementing an integration, inspect the current upstream README/API and keep the adapter thin.

### A. Real Android / ADB tool layer

Preferred current integration target:

- `TiagoDanin/Android-Debug-Bridge-MCP`
- https://github.com/TiagoDanin/Android-Debug-Bridge-MCP

Why we use it:

- it already works against real Android devices/emulators through ADB;
- it exposes MCP and CLI surfaces over one engine;
- it already supports package/app/process/system operations and package-filtered Logcat;
- we do not want to recreate ADB tooling.

The project should consume only the small subset needed by the diagnosis workflow, for example:

- collect/filter logcat
- inspect package information
- inspect process state
- optionally inspect system/service state if needed later

Do NOT fork this repo and turn our project into an ADB framework. Write a thin adapter.

Optional alternative only if the above integration creates unnecessary friction:

- `callstack/agent-device`
- https://github.com/callstack/agent-device

It supports Android/HarmonyOS and exposes CLI/MCP/typed APIs, but it is broader than we currently need. Do not introduce it unless it materially simplifies real-device validation.

### B. PTC runtime

Integration/reference target:

- `Chen-zexi/open-ptc-agent`
- https://github.com/Chen-zexi/open-ptc-agent

Why we use it:

- it already implements programmatic tool calling with code execution;
- it can expose MCP tools as Python-callable functions;
- it already addresses sandbox/tool-discovery infrastructure.

Our contribution is NOT “reimplement PTC”.

Keep a thin boundary such as:

```python
class PtcExecutor:
    def run(self, task, tools, context): ...
```

The first working version may call the existing runtime. If later the dependency is too heavy for our narrow workload, a tiny local implementation can replace it, but only after the real PTC path has been validated.

Do NOT copy large parts of open-ptc-agent into this repository.

### C. Log template clustering / duplicate compression

Use:

- `logpai/Drain3`
- https://github.com/logpai/Drain3

Why:

- it is a mature streaming log-template miner;
- it clusters messages such as repeated Binder errors into templates;
- it supports parameter masking and extraction;
- this is commodity functionality and should not be reinvented.

Feed Drain3 the unstructured message portion after parsing timestamp/PID/TID/level/tag.

Drain3 is a preprocessing component, NOT the project's novelty.

### D. Evaluation methodology reference

Reference, do not depend on:

- `camronh/pokebench`
- https://github.com/camronh/pokebench

Important idea to reuse:

- same model
- same tools
- same cases/world state
- change only the execution strategy
- keep ground truth deterministic and reproducible

Do not copy the Pokemon benchmark or its domain. Our eval workload is Android diagnostic log triage.

### E. Evidence organization / Android diagnosis reference

Reference, do not depend on:

- `yuchuangu85/llm-anr`
- https://github.com/yuchuangu85/llm-anr

Important ideas to learn from:

- evidence-first analysis
- source-specific evidence
- high recall before premature blame
- filtered logs stored separately instead of dumping everything into the LLM context
- candidate root-cause chains remain evidence-backed candidates

Do NOT clone its entire ANR workflow. Our scope is broader than ANR but much smaller in overall platform size: high-noise Logcat/KMessage triage and execution strategy evaluation.

## 4. What belongs to this project vs external infrastructure

External infrastructure owns:

```text
ADB/device control              -> Android-Debug-Bridge-MCP
PTC sandbox/tool plumbing       -> open-ptc-agent
log-template clustering         -> Drain3
```

Our code owns:

```text
1. Android/container diagnostic workload abstraction
2. normalization of collected evidence into a minimal event model
3. primary-vs-cascading candidate triage logic
4. Direct / Fixed / PTC / Hybrid execution modes
5. a very small Hybrid follow-up policy
6. trace format
7. replay dataset + ground truth
8. evaluation + badcase regression
```

This separation is important for interviews. The user should be able to say:

> I deliberately reused mature ADB and PTC infrastructure. My contribution was turning a real Android-container debugging pain point into a measurable Agent execution problem, designing the Hybrid execution boundary, and building the evaluation/regression loop.

## 5. Frozen MVP data flow

The main pipeline should remain understandable on one screen:

```text
Android Emulator / Device                  Replay Case
          |                                    |
          +------------ EvidenceBackend -------+
                              |
                     normalized LogEvent[]
                              |
                  Drain3 / fixed preprocessing
                              |
                   candidate event collection
                              |
                  Direct / PTC / Hybrid mode
                              |
                     small evidence bundle
                              |
                         LLM reasoning
                              |
     primary candidate + propagation candidate + evidence
                              |
                           evaluation
```

### Input

At minimum:

- Logcat
- optional KMessage/kernel-like log
- target package
- symptom description
- optional failure timestamp

### Output

Keep output minimal and structured:

```json
{
  "primary_candidates": [
    {
      "event_id": "...",
      "summary": "...",
      "score_or_confidence": 0.0,
      "evidence_ids": ["..."]
    }
  ],
  "propagation_chain": ["event-a", "event-b", "event-c"],
  "supporting_evidence": ["..."],
  "noise_clusters": ["..."],
  "missing_evidence": []
}
```

The exact schema may be simplified if needed. Do not create a complex ontology.

## 6. Execution modes that the final project should support

### Mode A — Raw Log baseline

This represents the current real-world workflow:

```text
raw Logcat/KMessage -> LLM
```

Purpose: establish whether our processing is actually useful.

### Mode B — Fixed pipeline baseline

Plain deterministic Python:

```text
parse -> normalize -> Drain3 cluster -> deduplicate -> time/process relation -> top candidate events -> LLM
```

Purpose: answer the interview question:

> Why PTC instead of a normal Python script?

### Mode C — PTC

The model receives a small set of available operators/tools and generates code to select and combine evidence, for example:

- filter by time window
- group by template/tag/component
- first_seen
- join Logcat and kernel events
- correlate PID/process/component
- rank candidates

PTC is responsible for deterministic high-volume manipulation. It must NOT directly claim semantic causality.

### Mode D — Hybrid

Keep Hybrid deliberately simple:

1. PTC performs first-pass triage.
2. LLM decides whether evidence is sufficient.
3. If insufficient, allow **at most one** Direct Tool Calling evidence-acquisition round.
4. Re-triage the new evidence.
5. Produce final diagnosis.

Set an explicit bound such as:

```python
MAX_INVESTIGATION_ROUNDS = 1
```

Do not build an autonomous indefinite exploration loop.

## 7. Minimal event representation

Use a small event model. Do not build a general log ontology.

A likely shape:

```python
@dataclass
class LogEvent:
    event_id: str
    source: Literal["logcat", "kmsg"]
    timestamp: datetime | None
    pid: int | None
    tid: int | None
    level: str | None
    tag: str | None
    message: str
    template_id: str | None = None
    template: str | None = None
```

Additional fields should only be added after a concrete eval case proves they are needed.

## 8. Candidate ranking: keep it explainable

Do NOT implement a general causal inference system.

The first fixed baseline may use only simple signals such as:

- temporal proximity to the user-visible failure
- first occurrence / newly appearing template
- target package/PID relation
- component/process relation
- occurrence before downstream errors
- duplicate/repetition penalty

For example, conceptually:

```text
candidate_score =
    temporal_score
  + first_seen_score
  + target_relation_score
  + component_relation_score
  - repetition_penalty
```

Exact weights can initially be heuristic and later tuned only if eval shows a need.

The LLM performs the semantic distinction between:

- likely primary failure
- downstream symptom
- unrelated noise

## 9. Evaluation philosophy

Evaluation is one of the user's main contributions, not an afterthought.

We need two complementary validation paths.

### Real E2E validation

Use Android Emulator or a real Android device:

```text
faulty test app
 -> real adb
 -> Android-Debug-Bridge-MCP
 -> collect actual evidence
 -> project pipeline
 -> diagnosis
```

Start with one intentionally crashing test app/case. We do NOT need to emulate the full HarmonyOS Android container at home.

This path answers:

> Can the system actually run against a device?

### Replay evaluation

Use fixed synthetic/recorded-like cases with ground truth.

This path answers:

> Does execution strategy A perform better than B on the same evidence?

Never use private company logs, internal commands, or proprietary source code in this repository. Build synthetic cases that reproduce generic failure shapes.

## 10. Initial eval categories

Do not create 50 cases immediately. Start with 8–10 cases only after the parser/backend foundation works.

Cases should eventually cover shapes such as:

1. obvious Java crash
2. native crash / SIGSEGV
3. service dies first -> Binder failures -> app failure
4. OOM/LMK -> service death -> cascading errors
5. many pre-existing unrelated ERROR lines
6. hundreds of repeated downstream errors hiding one early primary event
7. two plausible root-cause candidates
8. missing evidence requiring one follow-up query
9. Logcat/KMessage cross-source correlation
10. tool/evidence acquisition timeout or unavailable source

Ground truth should identify:

- expected primary event(s)
- required evidence
- expected propagation sequence where applicable
- known noise clusters

## 11. Metrics — keep the first version small

Only track metrics that are easy to explain in an interview:

- Root Cause Top-1
- Root Cause Top-3
- Evidence Recall
- Noise Reduction Ratio
- Context/Input Tokens
- Model Turns
- Latency

Trace other details if useful, but do not create a giant metrics framework.

A run trace can remain JSONL and include:

```json
{
  "case_id": "service_cascade_001",
  "mode": "ptc",
  "model_turns": 2,
  "tool_calls": 6,
  "input_tokens": 4200,
  "latency_ms": 3500,
  "primary_candidates": ["event-17"],
  "evidence_ids": ["event-17", "event-22"],
  "success": true
}
```

## 12. Milestones and stop conditions

Do not implement all milestones at once.

### M0 — scope, replay, normalized event foundation

Current task.

Deliver:

- minimal LogEvent parser for current fixtures
- loader for Logcat + optional KMessage
- replay backend
- current seed cases load successfully
- unit tests
- integration notes documenting how real ADB/PTC/Drain3 will attach later

DO NOT implement PTC or Hybrid yet.

### M1 — real ADB E2E

Deliver:

- Android Emulator/device connectivity
- thin adapter to Android-Debug-Bridge-MCP (or CLI surface of same project if simpler)
- collect actual package-filtered Logcat and basic package/process state
- one intentionally crashing Android app/case runs end-to-end

Stop once one case works. Do not expand the tool surface.

### M2 — fixed triage baseline

Deliver:

- Drain3 integration
- duplicate compression
- simple first-seen/time/process/component candidate ranking
- Raw vs Fixed comparison

### M3 — PTC

Deliver:

- thin adapter to open-ptc-agent or a verified equivalent execution path
- expose only the operators/tools required by this workflow
- same model, same tools, same case, change only execution mode where possible
- Fixed vs PTC evaluation

**This is already sufficient for a resume if the results are meaningful.**

### M4 — Hybrid, optional but desirable

Only add if M3 badcases show that existing evidence is sometimes insufficient or PTC overcommits before semantic feedback.

Deliver:

- one bounded Direct Tool Calling follow-up round
- re-triage
- Always PTC vs Hybrid comparison

### Hard stop

If M3 already demonstrates a useful and explainable result, do not expand scope unless a concrete interview-relevant gap remains.

## 13. What NOT to optimize during implementation

Do not spend time on:

- perfect names
- elaborate plugin systems
- generic dependency injection frameworks
- supporting every Android log format
- perfect timestamps for every Android version
- sophisticated causal graph theory
- dashboards
- deployment infrastructure
- large config systems
- refactors whose only benefit is aesthetics

If you notice a possible improvement, first ask:

1. Does this block a current milestone?
2. Does this change an eval result?
3. Does this materially improve interview explainability?

If all three are no, leave it alone and optionally add one TODO.

## 14. Interview narrative that the implementation must support

The architecture should make the following explanation truthful and easy:

> In our Android-container development workflow, engineers often collect Logcat and KMessage and give the raw logs to an LLM. The difficult part is that a failure produces many cascading errors, while unrelated errors may already exist, so the earliest visible ERROR is not necessarily the root problem. I built a small diagnostic-execution prototype around this real workflow. I reused mature ADB, PTC, and log-clustering infrastructure instead of rebuilding them. My work focused on structuring the diagnostic workload, comparing Raw/Fixed/PTC/Hybrid execution, and building an evaluation loop around primary-failure ranking, evidence retention, context reduction, and model round trips. The Hybrid path keeps deterministic high-volume log processing in PTC while returning to direct model/tool interaction when new semantic evidence is required.

Do not add architecture that makes this explanation harder.

## 15. Current M0 implementation task

Now implement **M0 only** in the existing repository.

### Tasks

1. Inspect the existing repository before modifying files.
2. Keep the existing compact directory structure unless a concrete blocker exists.
3. Implement a minimal parser for the current Android-style Logcat fixtures into `LogEvent` objects.
   - fields needed now: source, timestamp, pid, tid, level, tag, message
   - tolerate malformed/unparsed lines gracefully; preserve raw message if practical
4. Implement a loader that merges `logcat.txt` and optional `kmsg.txt` into a normalized event list.
   - do NOT infer causality
   - do NOT cluster yet
5. Keep `ReplayBackend` working.
6. Keep explicit future boundaries for:
   - real ADB backend
   - PTC executor
   - Hybrid executor
   but do NOT implement those runtime integrations yet.
7. Add `docs/INTEGRATION_NOTES.md` containing a concise plan for:
   - Android-Debug-Bridge-MCP integration in M1
   - Drain3 integration in M2
   - open-ptc-agent integration in M3
   - PokeBench and llm-anr as methodology/design references only
8. Add focused unit tests covering all current seed cases.
9. Keep functions and files small. Prefer obvious code over clever abstractions.
10. Run tests and report exactly what changed.

### Acceptance criteria

- `pytest` passes.
- all existing seed cases load into normalized events.
- Logcat + optional KMessage are both supported.
- no PTC/Hybrid implementation appears in M0.
- no new framework-style abstraction layer is introduced.
- `docs/INTEGRATION_NOTES.md` makes later reuse of upstream GitHub projects explicit.
- no individual source file should grow beyond ~250 lines without a concrete reason.

### Required final response from Codex

When M0 is complete, report only:

1. files changed
2. what is now runnable
3. tests run + result
4. current code-size summary
5. anything that blocks M1 real ADB integration
6. at most 3 next-step recommendations

Do not propose extra features beyond M1 unless explicitly asked.
