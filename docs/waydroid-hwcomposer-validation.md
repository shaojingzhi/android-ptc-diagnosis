# Waydroid hwcomposer public-incident validation

This case evaluates the existing evidence pipeline on a real Android-container startup failure for
which upstream supplied both raw Logcat and a source-level fix. It is a public-case adaptation, not a
local reproduction or a production dataset.

## Provenance and bounded input

The source is [Waydroid issue #1894](https://github.com/waydroid/waydroid/issues/1894), whose public
attachment contains 24,803 Logcat lines. The repository preserves an exact contiguous 157-line excerpt
(upstream lines 5,148–5,304) covering one hwcomposer crash and its immediate propagation. `source.yaml`
pins both the complete 3,020,442-byte attachment and excerpt SHA-256 values. No log line was rewritten;
the bounded input contains no detected credential, account, host path, IP address, or personal identity.

The issue maintainer states that the problematic crash had been fixed but that the fix was missing from
the LineageOS 20 vendor branch. The linked source change,
[`3b2f29d`](https://github.com/waydroid/android_hardware_waydroid/commit/3b2f29d0b41b89fef5c34ed054be589e58e3c7cb),
is titled “hwcomposer: Guard against missing wayland pointer” and guards cursor operations when no
Wayland pointer exists. This external source context is kept in the model-invisible assessment; it is
not added to the diagnostic prompt.

## Observed chain

The exact excerpt preserves:

- repeated read-only cgroup and task-profile errors during service startup;
- a SIGSEGV/null-pointer tombstone for `android.hardware.graphics.composer@2.1-service`;
- `wl_proxy_marshal` and `hwc_set` as the first two frames in `hwcomposer.waydroid.so`;
- init observing `vendor.hwcomposer-2-1` receive signal 11;
- SurfaceFlinger aborting after a `DEAD_OBJECT` HIDL call and receiving signal 6;
- a later AndroidRuntime `Surface$OutOfResourcesException: DEAD_OBJECT` record.

The upstream fix supports the missing-Wayland-pointer mechanism, while the Logcat alone supports only
localization to the hwcomposer null dereference and its propagation. The issue does not contain a
reporter-supplied post-fix success log, so this is not presented as a controlled before/after validation.

## Deterministic characterization

All 157 lines parse as Logcat and form 73 provenance-preserving groups. The key records remain available
through generic text and indexed retrieval: null-pointer cause at event 37, `wl_proxy_marshal` at 44,
`hwc_set` at 45, hwcomposer signal 11 at 67, SurfaceFlinger `DEAD_OBJECT` at 85, and SurfaceFlinger signal
6 at 113. Every normalized event belongs to exactly one compact group.

The unchanged FIXED baseline ranks event 19, an earlier `createProcessGroup(...): Read-only file system`
message, above the actual native crash chain. The null-pointer record is Top-3. This is a useful real-log
badcase: error severity and early occurrence do not establish incident relevance.

The registry-backed workflow accepts this case through the existing public-incident runner:

```bash
uv run python scripts/eval_public_android_container_workflow.py \
  --case waydroid_hwcomposer_1894 \
  --trace-file /tmp/waydroid-hwcomposer-trace.jsonl
```

The model sees a neutral incident description and the actual compact/log evidence. It does not see the
case directory name, issue discussion, fix commit, model-invisible assessment, or upstream conclusion.
