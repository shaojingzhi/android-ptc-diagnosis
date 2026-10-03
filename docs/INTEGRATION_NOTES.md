# Integration notes

M0 keeps the replay and normalized-event boundary local and deterministic. Later milestones should attach mature upstream infrastructure through thin adapters.

## M1: real ADB

Use `TiagoDanin/Android-Debug-Bridge-MCP` through its `adb-agent` CLI as the preferred Android/ADB tool layer. Install Android platform-tools and an emulator/device first, then install the upstream CLI:

```bash
npm install -g android-debug-bridge-mcp
adb-agent doctor
```

The minimal deliberate-crash fixture is in `eval/fixtures/java_crash_001/`. Build it as package `com.example.m1crash` with the local Android toolchain, install the resulting APK, and then run the manual flow from the repository root:

```bash
adb-agent app install /path/to/app-debug.apk
PYTHONPATH=src python3 scripts/m1_real_e2e.py --package com.example.m1crash --lines 1000
```

Pass `--device <serial-or-prefix>` when more than one device is available. The script runs `doctor`, clears Logcat, launches the fixture, waits briefly, reads unfiltered recent Logcat, feeds it to `parse_logcat()`, and requires `FATAL EXCEPTION` or an `AndroidRuntime` event. It intentionally does not rely on post-crash package-filtered Logcat, because the crashed PID may no longer exist.

Successful output includes `device reachable: yes`, the number of raw Logcat lines, the normalized `LogEvent[]` count, and `crash evidence found` greater than zero. Do not describe the Real E2E as passed until this command has run against a real emulator/device.

## M2: fixed triage and Drain3

Feed the unstructured message portion of normalized events to `logpai/Drain3` for template clustering and duplicate compression. Keep timestamp, PID/TID, level, tag, and source parsing in this project. Add only explainable first-seen, temporal, process/component, and repetition signals for the fixed baseline.

## M3: PTC

Use `Chen-zexi/open-ptc-agent` as the PTC runtime boundary. Expose a small set of log-processing operators and keep semantic diagnosis outside the runtime. Compare the same model, tools, cases, and world state while changing execution strategy. PTC is not required to beat FIXED on raw speed; its value is dynamic composition with fewer Direct Tool Calling round trips and less intermediate context.

## Method references

`camronh/pokebench` is a methodology reference for controlled execution-strategy comparisons, not a runtime dependency. `yuchuangu85/llm-anr` is an evidence-first Android diagnosis reference, not a workflow to copy. Neither project should be forked or vendored here.
