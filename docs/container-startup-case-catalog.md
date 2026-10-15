# Container startup diagnosis case catalog

这组数据用于离线评估 Android container runtime 启动失败诊断。它包含四个配对案例；每对的
`full` 与 `limited` 是同一设定机制的两种 evidence availability，不是八个独立样本。模型输入
位于各目录的 `case.yaml`、日志和 `runtime_state.yaml`；隐藏评估标准单独位于
`expected.yaml`。后续运行应向模型使用 `case.yaml` 中的中性 `model_incident_id`，不要把描述
机制的目录名放进 task。

当前开发机是 macOS，且没有 Docker/Podman。本轮没有修改宿主 mount、cgroup、namespace 或
内核设置，没有启动真实容器。A/B/C 是根据已核实接口语义生成的 `synthetic` replay；D 是
`public_case_adapted`。所有 Logcat/KMessage 行都是本项目自行生成或改编的评估数据，不是原始
production 日志；所有 `runtime_state.yaml` 字段都是模拟观测。时间值也是生成的，Logcat
wall-clock 与 KMessage monotonic time 没有对齐关系。

## Source ledger

| Pair | Provenance | Source and supported fact | Data boundary |
| --- | --- | --- | --- |
| A mount configuration | `synthetic` | [Linux 6.1 OverlayFS documentation](https://docs.kernel.org/6.1/filesystems/overlayfs.html) states that workdir must be empty and on the same filesystem as upperdir. Linux source at commit [`0fcb05b`](https://github.com/torvalds/linux/blob/0fcb05b72344f40806cbe810de8872f1698bf053/fs/overlayfs/super.c) returns `-EINVAL` and logs that workdir/upperdir must be under the same mount. | No mount experiment was run. The operation, paths, errno, kernel-style line, mount IDs, and init state are generated from the documented constraint/source path. This is only rootfs-preparation replay, not a container E2E reproduction. |
| B cgroup OOM | `synthetic` | [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html) defines `memory.max`, `memory.events`, `oom`, `oom_kill`, and local-only `memory.events.local`; it also states that a cgroup OOM does not kill tasks outside that cgroup. | No memory pressure was generated. PID, limit/current/peak, before/after counters, host memory, exit, and KMessage are generated. The case requires the counter delta plus cgroup identity and kill evidence; code 137 alone is intentionally insufficient. |
| C external dependency | `synthetic` | Linux [`connect(2)`](https://man7.org/linux/man-pages/man2/connect.2.html) documents `ECONNREFUSED` for a stream address with no listener. | No bridge programs were executed. Endpoint, connect result, listener/readiness state, and timestamps are generated. The complete case can narrow investigation to the bridge path but cannot establish why the bridge remains in `starting`. |
| D user namespace limit | `public_case_adapted` | runc issue [#3666](https://github.com/opencontainers/runc/issues/3666) reports rootless startup failure when `user.max_user_namespaces` or `kernel.pid_max` is zero. [`namespaces(7)`](https://man7.org/linux/man-pages/man7/namespaces.7.html) documents the per-user namespace limits and allocation failure; [`clone(2)`](https://man7.org/linux/man-pages/man2/clone.2.html) shows that syscall errors have multiple causes. runc source commit [`e6d915b`](https://github.com/opencontainers/runc/tree/e6d915bf2b9543c5b9fcba2ec99ef28edcb4fe65) validates that rootless containers require user namespaces. | No public log text was copied and no local syscall was run. The replay adapts the zero-limit mechanism, while the exact operation, probe output, and times are generated. It must not be presented as the issue reporter's original trace. |

Sources were checked on 2026-10-15. Versioned kernel documentation and fixed source commits are used
where practical. Public material supports the mechanism and observable interface, not the exact
synthetic incident sequence.

## A. OverlayFS upper/work mount mismatch

Paths:

- `eval/cases/startup_mount_config_full`
- `eval/cases/startup_mount_config_limited`

User-visible symptom: init never appears; the startup controller eventually reports a readiness
timeout. Similar symptoms could result from missing paths, permissions/LSM policy, a read-only upper,
unsupported upper filesystem features, or another rootfs-preparation failure.

Complete evidence includes the exact overlay operation/options, `EINVAL`, the documented kernel
message, distinct mount IDs for upper/work, and `container_init.state=not_started`. Existing log
queries, mount discovery/inspection, and process inspection can retrieve it. The allowed boundary is
“rootfs mount preparation rejected this upper/work path combination,” not “OverlayFS is broken.”

The limited version retains only a generic rootfs-preparation failure, target-not-mounted state, and
init-not-started state. The mount arguments, return, kernel line, and upper/work identities are marked
not collected. It should preserve multiple explanations. The highest-value next check is to collect
the exact mount options and return together with mountinfo/statfs identities for upper/work.

## B. Local cgroup memory limit

Paths:

- `eval/cases/startup_cgroup_oom_full`
- `eval/cases/startup_cgroup_oom_limited`

User-visible symptom: init PID 3100 appears, exits with signal 9/code 137, and readiness times out.
Alternatives include an operator/runtime kill, watchdog, host-wide pressure, and an ordinary crash
followed by external SIGKILL.

Complete evidence binds the PID to `/runtime/startup-02`, records `memory.max/current/peak`, samples
`memory.events.local` from `oom_kill=0` to `1`, includes a cgroup-specific kill record, and shows high
host `MemAvailable`. Existing process, cgroup, and indexed log tools expose all of this. The allowed
conclusion is a local cgroup OOM candidate during this observation window, not host-wide OOM.

The limited version retains PID/cgroup identity, configured `memory.max`, SIGKILL/code 137, and the
upper timeout, but counter/current/peak/host samples are explicitly not collected. A nonzero historical
counter is not substituted. The best next check is paired before/after `memory.events.local` plus
current/max/peak and host memory evidence.

## C. Host dependency not ready

Paths:

- `eval/cases/startup_dependency_unready_full`
- `eval/cases/startup_dependency_unready_limited`

User-visible symptom: container init stays running but never reaches ready. Alternatives include a
wrong endpoint, permissions, caller-side timeout/configuration, dependency crash, listener not bound,
or a healthy but slow dependency.

Complete evidence identifies `/run/host-bridge.sock`, records caller `ECONNREFUSED`, and supplies
dependency-side `state=starting`, `ready=false`, `listener_bound=false`, and registration loading.
Process and service inspection plus logs can narrow the problem to host-bridge readiness/connection.
It cannot explain why registration is still loading.

The limited version exposes only the caller's generic “dependency not ready” report and a placeholder
whose `observation_status` is `not_collected`; absence of a populated record is not evidence that the
service does not exist. The best next check is to identify the endpoint, capture the real connect
result, and inspect dependency-side process/readiness/listener state in the same interval.

## D. User namespace allocation compatibility

Paths:

- `eval/cases/startup_userns_limit_full`
- `eval/cases/startup_userns_limit_limited`

User-visible symptom: isolation-environment preparation fails before init and readiness times out.
Other explanations include missing `CONFIG_USER_NS`, permission/capability failure, chroot/nesting
constraints, seccomp, LSM policy, or other namespace resource limits.

Complete evidence records the requested flags, `clone3` returning `ENOSPC`, confirms
`CONFIG_USER_NS=y` and `/proc/self/ns/user`, reads `max_user_namespaces=0`, and records no observed
seccomp/LSM denial. Existing indexed log tools expose these probe results, while process inspection
confirms init never started. There is no dedicated live kernel-capability tool. The supported boundary
is that a namespace allocation limit/compatibility setting blocks this request—not missing kernel
support or a kernel bug.

The limited version contains only generic environment-preparation failure and not-collected probe
status. It must keep permission, policy, resource-limit, and feature-support explanations open. The
best next check collects syscall flags/errno, kernel/proc support, per-user limits, caller capabilities,
seccomp decision, and LSM audit together; a lone `EPERM` or `EINVAL` would remain ambiguous.

## Offline evaluation contract

Deterministic fixture characterization:

| Input | Events (Logcat + KMessage) | Compact groups |
| --- | ---: | ---: |
| Mount full / limited | 12 (11 + 1) / 10 (10 + 0) | 10 / 8 |
| Cgroup OOM full / limited | 14 (13 + 1) / 10 (10 + 0) | 12 / 8 |
| Dependency full / limited | 11 (11 + 0) / 9 (9 + 0) | 9 / 7 |
| User namespace full / limited | 12 (12 + 0) / 9 (9 + 0) | 10 / 7 |

For every complete input, offline tests require parser compatibility, stable normalized indices,
one-and-only-one compact group membership per raw event, and generic query reachability of the
distinguishing evidence. This is **evidence reachability**, not diagnostic success.

For every limited input, tests scan every model-visible file—request, logs, and runtime state—for
the pair's removed-evidence markers. Missing observations use `not_collected`/`collection unavailable`
rather than empty results. Evaluation should reward preserving the listed unknowns and choosing the
single highest-information next check; it should not require guessing the hidden mechanism.

The three gap classes are kept separate:

- **Data gap:** the limited fixture says an observation was not collected.
- **Tool gap:** D has recorded probe logs but no live kernel capability/policy primitive.
- **Active-query evidence:** full A/B/C runtime state is available through existing mount, cgroup,
  process, service, or discovery tools, but a model must choose to query it.

Semantic quality remains a later manual-audit task. No exact statement matching, LLM judge, ranking
rule, or case-specific diagnosis heuristic is added here.
