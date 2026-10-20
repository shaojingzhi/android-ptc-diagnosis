# Public Android-container incident validation

This validation closes a provenance gap left by the separate public Logcat and Linux-container
checks: it uses a public redroid issue attachment that contains both real Android Logcat and host-side
container/process observations. It asks whether the existing deterministic pipeline preserves the
evidence needed to distinguish **container boot state** from a **fatal Android system-process event**.
It does not claim that the underlying native fault has been diagnosed.

## Source

The input comes from [remote-android/redroid-doc #623](https://github.com/remote-android/redroid-doc/issues/623).
The reporter attached a diagnostic archive from redroid 11 running in Docker on Ubuntu 20.04 inside a
Windows VM. The archive advertised as ZIP is actually gzip-compressed tar data. `source.yaml` pins the
archive hash and every preserved file hash.

Three files are retained byte-for-byte, with filenames changed only for clarity:

| Local artifact | Upstream artifact | Role |
| --- | --- | --- |
| `logcat.txt` | `crash.txt` | Android crash-buffer Logcat |
| `process_snapshot.txt` | `ps.txt` | Android-container process snapshot |
| `container_inspect.json` | `container-inspect.txt` | Docker state/configuration snapshot |

The attachment's complete 8,166-line Logcat was checked offline but not committed because it contains
host and internal-network identifiers unnecessary for this bounded validation. Its SHA-256 remains in
the source ledger. The bounded artifacts retain public process, container, build, and internal-network
identifiers; no credential or personal account data was detected.

The issue maintainer concluded from `ps.txt` that the container boot had succeeded, while separately
calling out a fatal system crash that required investigation if it recurred. The reporter then retried
and confirmed that the connection worked. These comments support the boot-health interpretation; they
do not establish the native crash's root cause.

## Observed evidence

The Docker inspect artifact reports:

- `Status = running`, `Running = true`, `OOMKilled = false`, and `ExitCode = 0`;
- container start at `2024-02-23T03:24:49.976895714Z`;
- image `redroid/redroid:11.0.0-latest` with guest GPU mode.

The process snapshot contains `system_server` PID 1527, both zygotes, `adbd`, SystemUI, Launcher, and
other Android services. The snapshot has no embedded collection timestamp, so it is evidence of the
captured state, not a basis for an invented precise ordering against Logcat.

The crash buffer contains 76 raw lines: one buffer header and 75 normalized events. It records:

1. `system_server` PID 251 receiving SIGSEGV in `android.bg`;
2. a native backtrace beginning at `memtrack_proc_new` in `libmemtrack.so`;
3. Launcher and SystemUI reporting `DeadSystemException`;
4. an audio HAL service subsequently receiving SIGABRT.

The earlier crash PID and the process-snapshot PID are different. Together with the running container
state and maintainer interpretation, that supports “a fatal Android system-process crash occurred,
but the captured container reached a running state.” It does **not** prove why `memtrack_proc_new`
faulted, whether the reporter's original tty symptom was caused by it, or whether the recovered state
remained healthy.

## Deterministic pipeline result

All 75 diagnostic lines parse as standard Logcat; the buffer header is correctly excluded while the
original source line numbers remain 2–76. Drain3 produces 45 groups with every event assigned exactly
once. Indexed text retrieval locates:

| Evidence | Normalized indices |
| --- | --- |
| system_server SIGSEGV | 0 |
| `memtrack_proc_new` frame | 15 |
| Launcher/SystemUI `DeadSystemException` | 31, 34 |
| audio-service SIGABRT | 36 |

For this short, high-entropy crash buffer, compact model-view serialization is slightly larger than
the 11,614-byte raw input because group metadata outweighs duplicate removal. This is a useful boundary:
Drain3 compaction is not assumed to reduce every real input.

The unchanged generic FIXED baseline ranks the system_server SIGSEGV at event 0 as Top-1 using only
fatal severity, first occurrence, and an available wall-clock timestamp. That is useful crash
surfacing, but it cannot decide whether the container booted because the process and Docker snapshots
are deliberately not converted into synthetic LogEvents. State attribution still requires keeping
those independent observations alongside the ranked log candidate.

## Evaluation boundary

This is an evidence-preservation and attribution case outside the frozen replay benchmark. It does
not add a synthetic `runtime_state.yaml`, diagnosis label, Binder rule, or root-cause answer. A future
model run would need a neutral incident ID and must keep `assessment.yaml` model-invisible. No model or
paid API was called in this validation.
