# Integration notes

M0 keeps the replay and normalized-event boundary local and deterministic. Later milestones should attach mature upstream infrastructure through thin adapters.

## M1: real ADB

Use `TiagoDanin/Android-Debug-Bridge-MCP` as the preferred Android/ADB tool layer. The adapter should collect only package-filtered Logcat and the minimum package/process state needed for one deliberate Java crash case. Do not reimplement ADB or copy the upstream repository.

## M2: fixed triage and Drain3

Feed the unstructured message portion of normalized events to `logpai/Drain3` for template clustering and duplicate compression. Keep timestamp, PID/TID, level, tag, and source parsing in this project. Add only explainable first-seen, temporal, process/component, and repetition signals for the fixed baseline.

## M3: PTC

Use `Chen-zexi/open-ptc-agent` as the PTC runtime boundary. Expose a small set of log-processing operators and keep semantic diagnosis outside the runtime. Compare the same model, tools, cases, and world state while changing execution strategy. PTC is not required to beat FIXED on raw speed; its value is dynamic composition with fewer Direct Tool Calling round trips and less intermediate context.

## Method references

`camronh/pokebench` is a methodology reference for controlled execution-strategy comparisons, not a runtime dependency. `yuchuangu85/llm-anr` is an evidence-first Android diagnosis reference, not a workflow to copy. Neither project should be forked or vendored here.
