# Interview Notes

## 一句话定位

**Android container runtime evidence-driven diagnosis agent**：从 Logcat / KMessage 中整理可追溯证据，帮助工程师定位 container-runtime boundary 内最值得调查的 failure candidate。

它是 diagnosis copilot，不是自动修复系统，也不声称从日志证明严格因果关系。

## 最终架构

```text
Logcat / KMessage
    → normalize / Drain3 / compact evidence
    → Minimal PTC bounded evidence inspection
    → primary + evidence + propagation + uncertainty
    → bounded read-only follow-up
    → new runtime evidence
    → revised diagnosis
```

- normalized `LogEvent[]` 保留 source、原始 index 和可用时间信息。
- Drain3/compact 压缩重复模板，但保留 provenance；重复 evidence 不自动等于 semantic noise。
- Minimal PTC 只向模型暴露一个 `execute_code`，生成的程序通过有限 primitives/Skills 做 bounded evidence inspection。
- active diagnosis loop 最多执行两次 read-only runtime-state check；controller 负责身份解析、权限、参数、预算和 trace。

## 三个核心工程故事

### 1. 500-event noisy logs → provenance-preserving compact evidence

`binder_flood_001` 有 500 个 raw events。固定 triage/Drain3 将其组织为 13 个模板组；最大的重复 Binder failure group 有 350 条，service-unavailable group 有 80 条。compact view 给模型看 group identity、代表信息和计数，不把 500 条原文全部塞进 context，同时保留每个 raw event 的 provenance 和可回读 index。

关键取舍：压缩是 evidence management，不是未经证据支持的 noise classifier。需要深入时，Skill 只 materialize bounded indices，并用 source-local `evidence_view`；Logcat wall-clock 与 KMessage monotonic time 不做伪造的全局排序。

### 2. DeepAgents PTC 18w+ tokens → forensic attribution → Minimal PTC

第一次 PTC smoke 看到约 186k tokens 和 63.8s，不能直接得出“PTC 天然昂贵”。通过 trace 分解发现主要成本来自 DeepAgents 通用 middleware、filesystem/tool turns 和 context 往返，而不是生成 Python 本身。

因此保留低层 Daytona/execute-code 能力，去掉 DeepAgents high-level agent，做 Minimal PTC：模型只看到 `execute_code`，程序在 sandbox 内组合有限 primitives/Skills，再返回压缩 evidence。冻结的 binder baseline 达到 Top-1 正确、4 model turns、3 programs、16,058 tokens、invalid=false。这个实验同时保留 DIRECT 作为不同 baseline，而不是把 RAW、DIRECT、FIXED、PTC 混为一谈。

### 3. root-cause ambiguity → diagnosis scope / uncertainty → active diagnosis loop

静态日志经常能指出“哪里值得调查”，却不能说明“为什么发生”。因此输出使用：

- primary failure candidate
- supporting evidence
- propagation candidates
- uncertainties
- recommended next checks

模型决定要检查什么以及新 evidence 的含义；deterministic controller 只允许有限、只读、可审计的 runtime-state checks。这样系统可以收窄 uncertainty，而不把 supporting evidence 误写成 proven root cause。

## Active loop 最终案例：`runtime_service_unready_001`

静态链路是：runtime service startup timeout → service unavailable → Binder failures → app-visible startup failure → FATAL EXCEPTION。日志能定位 runtime service readiness failure，但不能解释 readiness timeout 的原因。

一次冻结 post-fix smoke 中：

1. `inspect_process_state(pid=2600, name="RuntimeService")` 成功解析到 canonical process `runtime_service`；结果是 process running、restart count 为 0。
2. 模型随后选择 `inspect_service_state(name="runtime_service")`；结果是 `state=starting`、`ready=false`、`dependency_ready=false`，required dependency 为 `vendor_bridge`。
3. uncertainty 从“readiness timeout 的底层原因未知”收窄到“vendor_bridge 为什么没有 ready 仍未建立”。
4. 模型提出第三个 service check，但 `MAX_FOLLOWUP_CHECKS=2` 阻止继续执行；没有 remediation。

这说明 active loop 已闭环：initial uncertainty → bounded check → runtime evidence → revised diagnosis。它仍然没有证明最终 root cause。

## Agent / deterministic boundary

### LLM 负责

- 选择当前 uncertainty 对应的下一步检查；
- 组合 bounded evidence；
- 解释 returned evidence；
- 更新 primary/evidence/propagation/uncertainties；
- 决定是否停止或请求下一次检查。

### Deterministic layer 负责

- group/process/service identity resolution；
- read-only allowlist 和参数校验；
- uncertainty index binding；
- follow-up budget、duplicate/no-new-information 检查；
- event-index range validation；
- provenance、latency 和错误 trace。

面试时可以概括为：**structure for identity, language for meaning**。身份、权限、边界和预算不能交给自然语言猜测；证据含义和下一步调查方向保留给模型。

## 关键 frozen numbers

| 对象 | 结果 |
|---|---|
| `binder_flood_001` | 500 raw events → 13 compact groups；最大重复 Binder group 350 条 |
| DeepAgents Skill-PTC exploratory result | 约 186,317 tokens，63.8s，invalid=true |
| Minimal PTC binder baseline | Top-1=true，4 turns，3 programs，16,058 tokens，invalid=false |
| Compact-DIRECT baseline | Top-1=true，3 turns，4 tool calls，9,039 tokens，约 10.5s |
| Active-loop `runtime_service_unready_001` smoke | Top-1/Top-3=true，2 次 read-only checks，invalid=false |

这些数字用于解释工程取舍，不代表跨模型、跨硬件的普适性能结论。

## Real-log incident context 的一次语义修正

最初的 incident scaffold 把时间窗口外的全部事件都计为 baseline。这个定义不可靠：采集窗口外的日志不一定代表“正常状态”，也可能属于更早的故障、历史噪声或无法判断的上下文。

修正后，调用方必须分别提供已观测的 `baseline_indices` 和 `incident_indices`；其余事件计入 `unassigned_count`。未做 clock alignment 的 KMessage 也保持 unassigned，但仍保留 raw index provenance。`not_seen_in_observed_baseline` 只表示某个 group 在**当前提供的 baseline 样本**中没有出现，不代表它在历史上第一次出现。

真实日志中的另一个关键挑战是：**ERROR 不等于与当前故障相关**。长期存在的 ERROR、瞬时告警和 incident 附近新出现的信号必须结合显式 baseline、incident context 和 supporting evidence 判断，不能只按 severity 推断相关性或因果性。

## 第一次 locally reproduced real-log validation

2026-10-13 在本地 Android 15 / API 35 Emulator 上重新构建并安装 M1 Java-crash fixture。采集过程使用 unfiltered Logcat，先记录约 5 秒 idle baseline，再启动 `com.example.m1crash` 触发已知 `IllegalStateException`，随后保留 incident 和 post-failure 日志。设备时区为 `Asia/Shanghai`，三个阶段的设备时间边界均在触发前记录；原始日志保留在本机、未提交仓库。

Deterministic pipeline 结果：

- 549 raw lines / 549 normalized events，其中 545 条有 timestamp；4 条 Logcat buffer header 被宽容保留为无 timestamp event。
- Drain3 得到 266 groups。
- 显式分类为 baseline 14、incident 371、unassigned 164；group metadata 汇总后仍严格等于 549。
- `FATAL EXCEPTION`、`M1 deliberate Java crash` 和 `Caused by: IllegalStateException` 均可通过原始 event index 定位。
- baseline 已存在重复 `BestClock`、`healthd` 和 `JobService` WARNING；post-failure 还出现与 fixture 无关的 `TaskPersister`、Google speech/network ERROR。这直接说明 ERROR/WARNING severity 不能替代 incident relevance 判断。

这次验证暴露了两个最小 ingestion gap：

1. `adb-agent` Logcat 行只有设备本地月日时间，现有 parser 生成 naive datetime；设备 `date` 边界带 `+0800`。incident helper 正确拒绝 naive/aware 混用。本次分析先核实设备时区，再显式使用 device-local naive 边界。最小后续修复应是在采集入口把已记录的设备时区附加到 Logcat timestamp，或统一规范化两侧时间，而不是静默丢弃时区。
2. 当前 center + integer before/after window 不适合精确表达这次分别记录的毫秒级 baseline/incident 起止边界。本次直接从精确边界生成显式 indices。若继续产品化 real-log ingestion，最小扩展是增加 explicit start/end helper，不改变 compact 或诊断规则。

完整 raw log 的提交前扫描没有发现邮箱、IP、host 用户路径或 API credential，但包含 emulator 的随机安装路径、长数字标识和系统 `redacted-pii` 标记，因此没有把原始文件公开提交。未来若形成 replay case，应从原始文件生成单独的审查/脱敏副本，并标记 `locally_reproduced_real_log`，不得伪装为 production data。

## Known limitations

- benchmark cases 是 synthetic replay，不是 Huawei production data。
- KMessage monotonic timestamp 与 Logcat wall-clock 没有可靠 clock alignment；mixed-source view 明确将 cross-source order 标为 unknown。
- runtime-state checks 当前由 replay fixture 驱动，不是 production device/runtime integration。
- Initial Minimal PTC 偶尔仍会出现 generated-code correction；trace 中也保留过一次 full `get_events()`，因此 bounded access 仍需持续审计。
- 没有自动 remediation；系统只提出 read-only next checks。
- 当前结果用于工程可解释性和面试展示，不足以声称通用 Android diagnosis accuracy。

## 面试中明确不能夸大的内容

- 不是 Huawei production data，也不是生产部署系统。
- primary failure candidate **不等于** proven root cause。
- Binder signal 的定位是 runtime/service health signal，不是 Binder 内部实现诊断。
- Java crash fixture 是 real-ADB E2E smoke test，不代表能够诊断任意 Android Framework 或 App 业务 bug。
- 项目定位是 Android container runtime diagnosis，尤其关注 container-runtime boundary；不是通用 Android Framework diagnosis agent。
- PTC 的价值是动态组合 bounded primitives、减少不必要的 round-trip/context，而不是宣称一定比 FIXED 更快。
