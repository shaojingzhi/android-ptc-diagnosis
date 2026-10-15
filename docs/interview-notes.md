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

Deterministic pipeline 的首次结果（修复 component-aware grouping 前）：

- 549 raw lines / 549 normalized events，其中 545 条有 timestamp；4 条 Logcat buffer header 被宽容保留为无 timestamp event。
- Drain3/compact 得到 266 groups。
- 显式分类为 baseline 14、incident 371、unassigned 164；group metadata 汇总后仍严格等于 549。
- `FATAL EXCEPTION`、`M1 deliberate Java crash` 和 `Caused by: IllegalStateException` 均可通过原始 event index 定位。
- baseline 已存在重复 `BestClock`、`healthd` 和 `JobService` WARNING；post-failure 还出现与 fixture 无关的 `TaskPersister`、Google speech/network ERROR。这直接说明 ERROR/WARNING severity 不能替代 incident relevance 判断。

这次验证暴露了三个最小 ingestion/grouping gap：

1. `adb-agent` Logcat 行只有设备本地月日时间，现有 parser 生成 naive datetime；设备 `date` 边界带 `+0800`。incident helper 正确拒绝 naive/aware 混用。本次分析先核实设备时区，再显式使用 device-local naive 边界。最小后续修复应是在采集入口把已记录的设备时区附加到 Logcat timestamp，或统一规范化两侧时间，而不是静默丢弃时区。
2. 当前 center + integer before/after window 不适合精确表达这次分别记录的毫秒级 baseline/incident 起止边界。本次直接从精确边界生成显式 indices。若继续产品化 real-log ingestion，最小扩展是增加 explicit start/end helper，不改变 compact 或诊断规则。
3. Drain3 的全局 message template 会把不同 component 的通用 stack frame 合并。此次 `at <*>` group 共 58 条，实际来自 `AndroidRuntime`、launcher search、Google speech、TapAndPay 和 GLSUser 等多个 tag/level，但旧 group representative 只显示第一条 `AndroidRuntime/E`。Agent 因而可能把其他进程的 stack frames 误当作 crash component evidence，baseline/incident comparison 也会失真。

最终修复没有修改 Drain3 算法，而是在所有消费方统一使用
`source + tag + level + template_id` 作为 deterministic group identity：

- 没有 tag/level 时，`None` 是明确的 identity component；没有 `template_id` 时按原始 event index 单独成组，避免 malformed lines 被意外合并。
- compact、`group_by_template()`、fixed triage 的 representative/repeat count，以及 `inspect_group_evidence()` 的 group ID 解析共享同一语义。
- 同组 PID 全部一致时才展示 PID；存在多个 PID 时返回 `null`，不再用第一条 PID 代表整组。
- 每个 raw event 仍恰好属于一个 group，完整 provenance indices 保留。

同一份本地 raw log 离线重放后，group count 从 266 变为 276；原 58 条
`at <*>` 被拆为 `AndroidRuntime/E=18`、`OneSearchSuggestProvider/D=11`、
`A/E=20`、`TapAndPay/W=5`、`GLSUser/W=4`。549 个 raw indices 均且仅归属
一个 group，`FATAL EXCEPTION` 和两处 `M1 deliberate Java crash` 仍可定位。

7 个现有 synthetic cases 的 group count 和 FIXED Top-3 在 deterministic 回归中没有变化。历史 experiment/trace 保持冻结；由于 group identity 语义已经变化，旧 trace 与未来包含跨 component 相同模板的数据不能按 group count 直接比较。

随后对 ingestion/time-window 做了独立修正：

- `parse_logcat()` 允许调用方显式提供 IANA device timezone 或 `tzinfo`，把 Logcat device-local time 转成 timezone-aware datetime；默认仍返回 naive datetime，保持旧 replay fixture 兼容，不假设 UTC。
- `beginning of main/kernel/system/crash/radio/events` 是 Logcat framing metadata，不再成为 normalized diagnostic event。每个保留事件记录 1-based `raw_line_number`，而 normalized event index 仍是过滤后列表中的位置，两者不再混淆。
- 新的精确 interval helper 接受 `start` / `end` datetime，按毫秒/微秒精度执行闭区间筛选；它拒绝字符串边界、倒置区间以及 naive/aware 混用，并继续排除无法映射到 wall clock 的 KMessage。

同一份 549-line capture 使用 `Asia/Shanghai` 和采集时记录的精确边界重放后：4 条 buffer header 被排除，得到 545 个 normalized events，且 545 个 timestamp 全部 timezone-aware；baseline / incident / unassigned 为 `14 / 371 / 160`，compact 汇总计数完全一致。component-aware compact 在清理后的输入上得到 275 groups；这是 ingestion stream 改变后的结果，不应与旧 266/276 group count 当作同版本指标比较。

原始 crash evidence 仍可追溯，但 normalized index 因 header removal 合理变化：`FATAL EXCEPTION` 从 123 变为 119（raw line 124），两处 `M1 deliberate Java crash` 从 125/140 变为 121/136（raw lines 126/141）。这说明对外报告真实采集证据时应同时保留 raw line provenance 与 normalized index。

## 第一次 locally reproduced real-log Agent smoke

在 commit `53fb8a0` 上对上述 capture 做了一次且仅一次 Compact-DIRECT smoke。模型没有接收仓库外的原始文件：offline gate 先验证 raw SHA256、545 events、275 groups、`14 / 371 / 160` phase counts 和 crash indices，再生成 545-line sanitized JSONL 与 545-line index map。85 个 event message/template 发生 path/UUID/long-ID redaction，所有结构化 PID/TID 使用稳定 pseudonym；index map 仅保留 normalized index、raw line number 和原文/脱敏文本 SHA256。最终 model task 和 trace 均未包含 credential、host path、Android private path、邮箱、IP 或 UUID。

冻结配置为 DeepSeek `deepseek-v4-pro`、temperature=0、thinking disabled；未启用 active diagnosis loop。唯一 run 的结果：

- 3 model turns，2 tool-calling rounds，4 次 tool calls；全部是 indexed `get_events`，没有 full-log read，单次最大 15 records。
- input/output/total tokens 为 `139,826 / 1,027 / 140,853`；总 latency `15.72s`。
- primary indices 为 `121 / 136 / 137`，分别对应 Activity launch wrapper exception、`M1 deliberate Java crash` cause 和 `MainActivity.onCreate` stack frame；evidence 中同时引用 `FATAL EXCEPTION` index 119。
- propagation indices 指向 crash reporting、force-finish、process death 和 launcher resume。所有 final indices 通过 range validation，run 没有 provider/tool error。
- 模型没有把 baseline 的 `BestClock` warning 或 Google speech、TapAndPay 等无关 ERROR 当作 primary；它还明确把 `BestClock` 识别为 baseline background noise。

这个 smoke 证明 real-log ingestion → incident-aware compact evidence → bounded indexed retrieval → valid diagnosis 的链路已闭环，但也暴露两个不能掩盖的问题：第一，122,654-byte / 275-group 初始 task 加上多轮 context 仍产生约 141k tokens；第二，模型 final notes 使用了 “Root cause” 和 “No environmental/system failure is implicated” 等过强措辞，超出项目偏好的 primary-candidate / evidence-bounded language。该结果按原样保留，不调 prompt、不重跑，也不覆盖历史 synthetic frozen benchmark。外部 trace 文件为 `compact-direct-trace-53fb8a0.jsonl`，SHA256 为 `3eab12983a4dd4243eb58749cc8d56eeb2858a3b602b68213e9e54fd24da5d46`。

对冻结 snapshot/task/trace 做离线成本归因后，122,654-byte task 可精确拆成：固定 instructions 461 bytes（0.38%）、template text 35,819（29.20%）、incident/baseline fields 29,042（23.68%）、其余 group metadata 55,770（45.47%）和其他 payload/JSON 1,562（1.27%）。因此已证实的问题不是 instruction 太长，而是把 275 个 group 的完整 template、phase counters 和重复 metadata 一次性全部放入首轮 context。199 个 group 还是 singleton，真实日志的高基数限制了仅靠 Drain3 压缩的收益。

冻结 trace 只有 aggregate `139,826 / 1,027 / 140,853` token usage，没有逐轮 usage；四个 indexed tool result 合计 21,407 bytes。无法据此真实拆分每轮 token，也不能把 byte/token 换算当成实际 provider usage。初始 task 和累积 context 很可能是主要成本来源，但“二级索引会按比例降低 token 且不损失准确率”仍是尚未验证的假设。

候选的最小后续设计是 two-level evidence index：首轮只提供按 incident novelty、request overlap、时间邻近和 source/tag/level 多样性确定性选出的 bounded detailed groups；其余 group 保留在稳定 `group_id` 的 deferred index 中，通过一个 bounded read-only query 按需获取。不能硬编码 crash 字符串/ground-truth index，也不能只筛 ERROR。全部 545 events、raw-line provenance 和 indexed access 必须保留，并先用 evidence reachability、source/tag/level/phase coverage、100% provenance coverage、query bound、determinism 和 task bytes 做离线验收。完整分析见 [real-log-cost-attribution.md](real-log-cost-attribution.md)。

随后实现了 opt-in 的最小 Two-level Evidence Index；在该历史实验版本中，默认 Compact-DIRECT 的 8-tool contract 和完整 compact prompt 保持不变。Level 1 使用四个通用且可审计的 selection reasons：`request_overlap`、`incident_phase`、`incident_time_proximity`、`source_tag_level_diversity`。Level 2 保留未展示的完整 groups，并增加只读 `query_deferred_groups`；它按全局 `group_id`、text、tag、source 或 phase 查询，最多返回 20 groups / 16,000 serialized bytes，不对子集重新编号，也不把 deferred groups 当作 noise。

同一份脱敏 545-event snapshot 的纯离线结果为：首次 task 从 122,654 bytes 降至 21,824 bytes（减少 82.21%），32 个 detailed groups / 243 个 deferred groups；新增 tool schema 是 558 bytes。Level 1 覆盖唯一的 logcat source、全部 5 种 level、baseline/incident/unassigned 三种 phase，以及 97 个 tag 中的 25 个。545 个 event index 均且仅归属一个 detailed/deferred group，全局 group IDs 仍严格为 `0..274`，重复构建结果完全一致。

这里必须区分 **reachability** 与 **discoverability**。评估用的三个已知 crash indices `119/121/136` 都仍可追溯；121 的 group 在 Level 1，119 和 136 在 deferred index。它们可以通过从 Level 1 已出现的 `AndroidRuntime` tag 发起通用 incident query 找到：该查询返回 7 groups / 2,447 bytes；target-package text query 返回 5 groups / 2,186 bytes。ground-truth indices 只用于事后评估，没有进入 selector。风险仍然存在：如果真正 evidence 与 request 没有词面重合、不靠近 incident time、也未被 diversity quota 选中，模型必须主动扩大查询；高基数 tag 和有界查询的截断也可能降低 discoverability。当前只证明 task-byte reduction、determinism 和 evidence reachability，尚未运行模型，因此不能声称实际 token 或 diagnosis accuracy 已改善。

完整 raw log 的提交前扫描没有发现邮箱、IP、host 用户路径或 API credential，但包含 emulator 的随机安装路径、长数字标识和系统 `redacted-pii` 标记，因此没有把原始文件公开提交。未来若形成 replay case，应从原始文件生成单独的审查/脱敏副本，并标记 `locally_reproduced_real_log`，不得伪装为 production data。

## Why higher-level diagnostic primitives are needed

Low-level primitives make raw evidence accessible, but asking the model to repeatedly resolve
candidate identity, compose time windows, preserve provenance, and enforce result bounds adds
planning burden that is unrelated to interpreting the failure. `inspect_failure_context()`
captures the common engineering step “inspect bounded evidence around this candidate” while
leaving the diagnosis itself to the reasoning layer.

The primitive performs bounded evidence acquisition only: it keeps normalized indices and raw
line provenance, prioritizes related records deterministically, respects source-local clocks,
and reports truncation and cross-source ordering limits. It does not rank failure candidates,
infer causality, or solve root-cause analysis.

### Evaluating higher-level diagnostic primitives

Hypothesis: exposing `inspect_failure_context()` might let the agent replace several low-level
evidence operations with one bounded contextual read while preserving diagnosis quality. At
commit `aa99c68`, `runtime_service_unready_001` was run once per arm with Compact-DIRECT,
DeepSeek `deepseek-v4-pro`, temperature 0, and thinking disabled. The prompt, case, ranking,
and all other tools were identical; only the variant exposed the ninth primitive.

| Arm | Top-1 / Top-3 | Model turns | Tool calls | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: |
| Baseline, 8 tools | true / true | 2 | 2 | 5,842 / 938 / 6,780 | 18.31s |
| Variant, 9 tools | true / true | 3 | 4 | 12,036 / 1,231 / 13,267 | 20.63s |

Both arms returned primary index 1 and identical evidence/propagation indices. The baseline used
two bounded indexed `get_events` calls and no full read. The variant first made the same two
indexed reads, also called `group_by_pid`, and only then selected
`inspect_failure_context(candidate_indices=[1, 23], before_seconds=1,
after_seconds=3, max_events=30)`. That call returned 24 Logcat events without truncation, so it
duplicated evidence the model had already materialized instead of replacing low-level planning.
Consequently this single run did **not** demonstrate lower planning cost: total tokens increased
95.7%, tool calls doubled, and latency increased 12.7% while Top-1/Top-3 stayed unchanged.

Safety remained bounded: there were no full reads or remediation actions, and both diagnoses
explicitly avoided proven-causality claims. The contextual call contained only the comparable
Logcat wall-clock domain, so it correctly reported `single_time_domain`; KMessage records were
read separately and the variant noted that their monotonic timestamps had no wall-clock order.
However, both arms returned empty `uncertainties` and `recommended_next_checks`, failing to retain
the case's expected unresolved readiness cause. One temperature-zero run per arm is not enough
to separate interface effects from provider variability, and tool-schema exposure alone does
not ensure that the model will choose the higher-level operation early. The supported conclusion
is only that `inspect_failure_context` changes the evidence acquisition interface and allows
evaluation of whether higher-level abstractions reduce agent planning burden; this experiment
did not show a reduction. Frozen traces are in
`experiments/runtime_service_unready_baseline_trace.jsonl` and
`experiments/runtime_service_unready_failure_context_trace.jsonl`.

### Tool semantic interface experiment

The team evaluated whether semantic tool contracts improve tool selection without adding
deterministic routing rules. At implementation commit `aa99c68`, one A/B run per arm used
`runtime_service_unready_001`, Compact-DIRECT, DeepSeek `deepseek-v4-pro`, temperature 0, and
thinking disabled. Both arms exposed the same eight tools with byte-identical names and parameter
schemas; only their description strings differed. The unified tool surface was an evaluation-only
harness and did not change production orchestration.

| Arm | Top-1 / Top-3 | Turns | Tool calls | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: |
| Current descriptions | true / true | 7 | 16 | 36,291 / 1,660 / 37,951 | 27.71s |
| Semantic descriptions | true / true | 6 | 17 | 33,937 / 1,582 / 35,519 | 25.95s |

The first tool remained `get_events` in both arms, so richer descriptions did not make the
higher-level primitive the initial evidence operation. They did change its timing: the current-
description arm called `inspect_failure_context` twice as calls 15 and 16, after broad group and
runtime-state exploration; the semantic arm called it once as call 2, immediately after the first
indexed read. Duplicate materialized event references fell from 25 to 6, model turns fell from 7
to 6, and total tokens/latency each fell about 6.4%. This was not a general tool-efficiency win:
total calls increased from 16 to 17, and both arms produced eight invalid runtime-state locator
requests.

Both diagnoses kept Top-1/Top-3 correct. The semantic arm returned only index 1 as primary,
whereas the baseline also promoted KMessage supporting evidence 24/25 to primary candidates;
both returned the same evidence indices and empty `uncertainties` / `recommended_next_checks`.
Safety was mixed. Neither arm requested remediation, and the baseline explicitly preserved unknown
cross-source ordering. The semantic arm instead ended with “Causality is inferred” and treated a
missing replay record as evidence that `vendor_bridge` was unavailable, which exceeds the intended
evidence boundary. Therefore the supported conclusion is narrow: semantic descriptions changed
selection timing and reduced duplicate materialization in this run, but did not reduce total tool
calls or reliably preserve causal language. One run per arm cannot establish a stable model effect.
Frozen traces are in
`experiments/runtime_service_unready_tool_description_baseline_trace.jsonl` and
`experiments/runtime_service_unready_tool_description_semantic_trace.jsonl`.

### Identity discovery experiment

Adding state-inspection tools increases capability, but the model still needs to know which
runtime identifiers are valid. `discover_runtime_entities(entity_type=...)` separates entity
resolution from entity inspection: it returns only canonical process names/PIDs, service names,
or mount paths present in the replay state, with their attributes and `read_only=true`. It does
not create aliases, infer absent entities, or return diagnosis fields.

The hypothesis was that optional identity discovery could reduce invalid locator guesses without
changing diagnosis semantics. At implementation commit `ec3f85b`,
`runtime_service_unready_001` was run once per arm with Compact-DIRECT, DeepSeek
`deepseek-v4-pro`, temperature 0, and thinking disabled. Both arms reused the same prompt and the
same eight existing tools; the variant only added the discovery tool. There was no routing rule or
instruction telling the model to call it.

| Arm | Top-1 / Top-3 | Turns | Tool calls | Unknown locator errors | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Baseline, 8 tools | true / true | 5 | 11 | 6 | 26,782 / 1,181 / 27,963 | 20.97s |
| Variant, 9 tools | false / false | 7 | 17 | 4 | 28,314 / 1,226 / 29,540 | 21.93s |

The baseline guessed six unavailable identifiers, including `RuntimeService`, two absent PIDs,
the target package as a process name, and `vendor_bridge` as both service and process. It still
completed with primary index 1 and a valid evidence-bounded diagnosis. The variant first guessed
the nonexistent service `runtime`, then discovered the canonical service `runtime_service` and
successfully inspected it. It subsequently discovered the sole process (`runtime_service`, PID
2600) and mount (`/container/runtime`) and successfully inspected the canonical mount path.
Discovery therefore reduced unknown-locator errors from 6 to 4 and demonstrated that returned
canonical identities were usable.

That partial identity improvement did not improve the run overall. The model called discovery
three times, continued guessing absent `vendor_bridge` and PID 2500, expanded into filter,
context, cgroup, and mount checks, and requested another tool round after the six-round budget.
The controller stopped it without a final diagnosis. Compared with baseline, tool calls rose by
6, turns by 2, total tokens by 1,577 (5.6%), and latency by 0.96s (4.6%). Thus this single A/B run
does **not** support claiming that identity discovery improved diagnosis quality or efficiency.
It shows a narrower result: discovery can resolve canonical query targets when selected, but
capability availability alone does not ensure discovery-before-inspection or bounded planning.

Safety conclusions are also limited. The discovery responses contained only fixture-backed
entities and read-only state, so the primitive itself neither invented entities nor enabled
remediation. The baseline final diagnosis avoided a proven-causality claim. The variant produced
no final diagnosis, so causal-language and remediation safety cannot be evaluated from its output;
the model's remaining invalid guesses must not be mistaken for discovered entities. With only one
run per arm, provider variability is not separable from the interface effect. Frozen traces are in
`experiments/runtime_service_unready_identity_discovery_baseline_trace.jsonl` and
`experiments/runtime_service_unready_identity_discovery_variant_trace.jsonl`.

### Structured identity experiment

The identity-discovery experiment showed that inventory access alone did not stop the model from
later inventing free-form service names, process locators, and mount paths. The follow-up
hypothesis was that separating entity resolution from inspection with validated references could
constrain those invalid actions. An evaluation-only `EntityReference` contains an inventory-backed
`entity_type`, deterministic `entity_id`, and canonical name. Discovery returns these references;
the three `*_by_ref` inspection variants validate type, ID, and canonical identity against the
same runtime snapshot before performing the existing read-only inspection. The original
free-form tools and production tool surface remain unchanged.

At implementation commit `75754b9`, `runtime_service_unready_001` was run once per arm with the
same Compact-DIRECT prompt/executor, DeepSeek `deepseek-v4-pro`, temperature 0, and thinking
disabled. The baseline exposed the existing eight-tool free-form interface. The variant retained
the same evidence tools and cgroup inspection, replaced process/service/mount inspection with
typed-reference variants, and added discovery. Its JSON schemas enumerated only entity IDs
generated from the fixture inventory; no discover-first routing rule or prompt instruction was
added.

| Arm | Top-1 / Top-3 | Turns | Tool calls | Invalid P/S/M locators | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Free-form, 8 tools | true / true | 7 | 18 | 10 | 37,615 / 1,888 / 39,503 | 33.09s |
| Typed reference, 9 tools | true / true | 4 | 8 | 0 | 19,145 / 1,187 / 20,332 | 23.29s |

The free-form arm attempted `RuntimeService`, absent PIDs 2500/2400, nonexistent mount paths
`/data`, `/system`, `/vendor`, and `/vendor_bridge`, and `vendor_bridge` as both service and
process. It also made one invalid free-form cgroup lookup outside the three typed entity types, so
its total failed state-locator count was 11. The typed arm performed service and process discovery,
then passed the returned `service:runtime_service` and `process:runtime_service` references
unchanged to the corresponding typed inspections. It later discovered the sole canonical mount
reference without inventing another path. All eight calls succeeded; there were no failed
exploration loops or invalid locator arguments.

Both arms returned primary index 1 and identical evidence and propagation indices, preserving
Top-1/Top-3. In this one pair, typed references reduced tool calls by 55.6%, turns by 42.9%, total
tokens by 48.5%, and latency by 29.6%. This supports the narrow claim that an inventory-derived
typed interface can constrain invalid locator exploration in this case. It does not establish a
general accuracy or efficiency improvement: there was one sample per arm, and the variant changed
both the nested reference representation and the dynamic schema enums, so their individual effects
cannot be separated.

Safety remained imperfect despite the cleaner tool sequence. Neither arm requested remediation,
and the variant used only fixture-backed references. However, both returned empty `uncertainties`
and `recommended_next_checks` even though why `vendor_bridge` was not ready remained unestablished.
The free-form arm over-interpreted failed lookups as proof that the dependency was missing; the
typed arm used the more cautious phrase “consistent with ... unavailable/not-ready” but still did
not retain the evidence gap. Typed identity validates *what can be inspected*; it does not validate
the semantic interpretation of absence, prove causality, or replace uncertainty handling. Frozen
traces are in `experiments/runtime_service_unready_typed_reference_baseline_trace.jsonl` and
`experiments/runtime_service_unready_typed_reference_variant_trace.jsonl`.

### Evidence-grounded diagnosis contract experiment

Earlier iterations improved evidence retrieval and locator reliability, but they did not ensure
that final language preserved the difference between a fact, an interpretation, and a useful
debugging hypothesis. The evaluation-only contract therefore represents each claim with a
`statement`, `status` (`observation`, `inference`, or `hypothesis`), and non-empty event
`evidence_refs`. Inferences also require a reasoning summary. Hypotheses require an evidence-support
level (`low`, `medium`, or `high`), explicit missing evidence, and suggested validation. Separate
evidence-gap records state the open question and what evidence is absent. Deterministic validation
rejects missing required fields and out-of-range event references; it intentionally does not claim
to decide whether evidence semantically entails a statement.

At implementation commit `ac16e66`, `runtime_service_unready_001` was run once per arm with the
same typed-reference nine-tool surface, Compact-DIRECT evidence, system instructions, executor,
DeepSeek `deepseek-v4-pro`, temperature 0, and thinking disabled. The compact task was byte-identical
except for `output_schema`: 2,918 bytes for the existing `DirectDiagnosis` contract and 3,375 bytes
for the structured contract. No prompt tuning or discover/inspect routing was added.

| Contract | Top-1 / Top-3 | Turns | Tool calls | Input / output / total tokens | Latency | Final diagnosis |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Existing free text | true / true | 6 | 14 | 38,594 / 1,934 / 40,528 | 29.87s | valid |
| Evidence-grounded | false / false | 7 | 13 | 47,534 / 1,266 / 48,800 | 20.50s | none; round limit |

The baseline correctly selected primary index 1 and produced four uncertainties and four next
checks. It explicitly noted the missing `vendor_bridge` record and unknown KMessage/Logcat clock
alignment. However, its notes simultaneously called `vendor_bridge` the “root blocker,” said the
probe events “confirm” that explanation, and claimed mount and memory evidence ruled out those
causes. Those statements are stronger than the available replay evidence. The current format has
no machine-readable claim/evidence relation, so a comparable grounding percentage cannot be
computed without retrospective human labeling; textual index mentions are not equivalent to
structured evidence references. Its useful dependency hypothesis also remained mixed into prose
rather than being explicitly labeled with missing evidence and validation.

The structured arm gathered relevant log, service, process, mount, and cgroup evidence without
locator errors, but it never emitted a final contract. On its sixth tool round it called
`inspect_group_evidence(group_ids=[0,6,7], event_count=3)`, confusing the selected group count with
the 26-event replay size; the deterministic tool rejected the call. The next model invocation
requested another tool round, so the six-round safety cap stopped the run. Consequently there are
no structured claims to score, no grounded-claim percentage, and no hypothesis-quality result.
Its 20.4% higher total-token use reflects the extra accumulated turn/context; its lower latency is
not a diagnosis-efficiency win because the run ended without a final answer.

This negative result does not show that claim typing worsens reasoning, nor that it improves it.
It shows that a richer final schema can coexist with a new planning failure and may increase the
burden of reaching a final response. One run cannot separate provider variability from contract
effects, and event-only references still cannot directly identify runtime-state tool results.
Future work would need an independently justified evidence-reference model and broader evaluation,
not case-specific prompt tuning or a rerun of this sample. The project goal remains reliable
debugging assistance: confirmed facts, plausible hypotheses, explicit unknowns, and actionable
read-only checks—not automatic or proven root-cause discovery. Frozen traces are in
`experiments/runtime_service_unready_grounded_contract_baseline_trace.jsonl` and
`experiments/runtime_service_unready_grounded_contract_variant_trace.jsonl`.

### Simplified evidence-grounded diagnosis contract experiment

The rich claim contract made observations, inferences, hypotheses, confidence, missing evidence,
and validation directions explicit, but its first evaluation never reached a final response. A
smaller evaluation contract therefore kept only observation/hypothesis claims, generic evidence
references, separate evidence gaps, and next checks. A generic reference can identify a normalized
log event, replay runtime-state record, or canonical entity. Deterministic validation checks the
reference format and verifies it against event bounds or the runtime inventory; it does not judge
whether the referenced evidence semantically entails the statement.

At implementation commit `abee638`, `runtime_service_unready_001` was run once per arm with the
same typed-reference nine-tool surface, Compact-DIRECT evidence, system instructions, executor,
DeepSeek `deepseek-v4-pro`, temperature 0, and thinking disabled. The only prompt difference was
the requested final schema: 3,375 task bytes for the rich contract and 3,122 for the simplified
contract. Hypothesis generation remained available in both arms.

| Contract | Final / schema valid | Turns | Tool calls | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: |
| Rich | no / no | 7 | 15 | 46,051 / 1,284 / 47,335 | 19.93s |
| Simplified | yes / yes | 5 | 13 | 23,718 / 2,078 / 25,796 | 29.57s |

The rich arm completed all six permitted tool rounds without tool errors, then requested a seventh
round and was stopped before returning claims. It therefore has no completion, grounding, or
hypothesis-quality score. The simplified arm returned after four tool rounds. It produced eight
claims—five observations and three hypotheses—and all eight had structurally valid references;
the reference validator found zero invalid IDs. It preserved three evidence gaps and five read-only
next checks. Total tokens were 45.5% lower and model turns fell from seven to five in this pair.
Latency was higher despite completion, so it does not support a latency-efficiency claim. The run
also made two invalid free-form cgroup probes (`vendor_bridge` and `container`) before finishing;
the simpler output contract did not solve all tool-planning errors.

Manual semantic audit is less favorable than the structural 100% grounding number. One observation
correctly cited the runtime-service entity for readiness state but also mentioned PID without a
process-state reference. Another cited the running process and then embedded the inference that it
was “stuck waiting on its dependency” inside an observation. In other words, typed references
validated identity, not entailment, and the model could still mix interpretation into a fact. Of
the three hypotheses, the medium-confidence `vendor_bridge` readiness hypothesis was useful and
preserved its missing evidence; treating Binder failures as propagation was also diagnostically
useful, though its statement remained categorical. The low-confidence ThermalHAL hypothesis was
weak and generated a correspondingly low-value next check. The output did not preserve the known
KMessage/Logcat cross-source ordering gap.

This one pair supports a narrow completion result: the simplified contract produced a valid final
where the rich contract did not, with fewer turns and tokens. It does not prove that simplification
generally improves reasoning quality. One run cannot separate provider variability from schema
effects, and a structurally valid reference can still be semantically incomplete or attached to a
misclassified claim. The useful design target is therefore not maximal metadata or minimal fields
at any cost, but the smallest contract that keeps hypotheses, explicit evidence gaps, and actionable
validation while remaining usable by the agent. Frozen traces are in
`experiments/runtime_service_unready_simplified_contract_rich_trace.jsonl` and
`experiments/runtime_service_unready_simplified_contract_variant_trace.jsonl`.

### Structured Evidence Representation experiment

Earlier work addressed how bounded evidence is retrieved. This experiment asked a different
question: whether representing each cited fact as an explicit, typed, inspectable `EvidenceItem`
makes claim grounding clearer. An item has a unique `evidence_id`, one of three sources
(`log_event`, `runtime_state`, or `entity`), a typed subject identity, and—for observed log/runtime
state—an explicit field/value observation. Claims cite only item IDs. Deterministic validation
checks source and identity syntax, event bounds or replay inventory membership, unique IDs, and
claim-to-item references. It deliberately does not decide whether an observation proves a claim.
The existing `SimplifiedDiagnosis`, `DirectDiagnosis`, compact format, primitives, and frozen
outputs remain unchanged; the structured contract is an additive evaluation model and does not
implement ownership, component attribution, ranking, or root-cause rules.

At implementation commit `f9ed28d`, `runtime_service_unready_001` was run once per arm with the
same DeepSeek `deepseek-v4-pro` settings (temperature 0, thinking disabled), system-prompt hash,
nine-tool schema hash, compact evidence, and six-tool-round executor. The baseline requested the
current heterogeneous `EvidenceReference` objects. The variant requested an `EvidenceItem`
registry and claim references to registry IDs. The variant schema made its initial task 959 bytes
larger (5,511 versus 4,552 bytes); no prompt or validator was changed after seeing either result.

| Evidence representation | Accepted final | Turns | Tool calls | Input / output / total tokens | Latency |
| --- | --- | ---: | ---: | ---: | ---: |
| Existing references | no | 6 | 9 | 34,945 / 1,854 / 36,799 | 139.86s |
| Structured `EvidenceItem` | no | 6 | 11 | 31,278 / 2,529 / 33,807 | 135.57s |

The baseline response parsed as the simplified schema but failed deterministic validation on its
first claim because it emitted bare log ID `"1"` rather than canonical `"event:1"`. The structured
response did not pass schema validation: eight log subjects used bare indices (`"0"`, `"1"`, and
so on) instead of `event:<index>`, and one cgroup subject used `"runtime_service"` instead of
`cgroup:runtime_service`. Before the final response it also made one incorrect
`inspect_group_evidence(event_count=3)` call, corrected it to 26, and continued. These failures were
kept as the only formal pair; neither arm was rerun.

Consequently, accepted-claim grounding percentage, evidence precision, evidence clarity,
unsupported-observation count, hypothesis support, and diagnosis quality are **not scorable** for
this pair. The baseline has at least one invalid reference. The variant has nine malformed subject
identities, but claim-to-item validity cannot be assessed because validation stopped before a
`StructuredEvidenceDiagnosis` existed. Lower total tokens in the invalid variant (8.1% below the
also-invalid baseline) is not evidence of efficiency or quality. Both arms spent six model turns,
and the variant used two more tools.

This negative result shows that typed evidence makes identity mistakes more visible, but does not
show that it improves semantic grounding. It also exposed a trace limitation: the one-shot harness
stored validation errors but not the rejected raw final payload, so manual claim-level semantics
cannot be reconstructed without an impermissible rerun. Future evaluation should preserve a
redacted rejected payload and independently improve schema affordances before another predeclared
experiment. That is preparation for later failure-localization research, not component attribution
itself. Frozen traces are in
`experiments/runtime_service_unready_structured_evidence_baseline_trace.jsonl` and
`experiments/runtime_service_unready_structured_evidence_variant_trace.jsonl`.

### System-managed evidence and rejected-final audit

The structured-evidence experiment exposed two separate engineering problems. First, the model was
asked to reproduce the evidence registry itself and produced malformed subject identities. Second,
the one-shot evaluator kept validation errors but discarded the rejected final text, so the failed
answer could not be audited claim by claim. The historical traces remain unchanged and their missing
payloads were not reconstructed.

Final handling now records a complete, secret-redacted text response before parsing. Additive trace
fields then record the parsed payload when available, structured validation issues with field paths,
and one explicit state: no final, JSON parse failure, schema failure, reference failure, or accepted
final. A round-limit stop remains “no final” with its real stop reason; rejected finals remain
available for human review but do not count as accepted diagnoses. Older traces omit these fields
and still load with an unknown legacy final status rather than being mislabeled.

An opt-in Compact-DIRECT path now owns a run-local evidence registry. The system converts each
model-visible compact group into a `log_event` EvidenceItem, using the representative normalized
event as its subject without guessing a process or service from log text. Recognized tool results
add exact normalized event records, returned index matches, compact groups, discovered entity
identities, or individual runtime-state field/value observations. IDs are deterministic hashes of
source, typed subject, field, and actual value; repeated evidence reuses the ID and accumulates
provenance pointers to compact-group or tool-result paths. The registry is reset on every run and
never loads replay runtime inventory that a tool did not return.

The model now returns only claims, evidence gaps, recommended next checks, and evidence-ID
references. It cannot author or rewrite EvidenceItems in this path. The deterministic layer owns
evidence identity, source fidelity, uniqueness, provenance, reference existence, validation stage,
and tool-round budget. The model still owns observation/hypothesis language, interpretation,
uncertainties, and suggested diagnostic checks. Entity evidence means only that an identity was
shown; state claims require a separate runtime-state observation.

Some tool returns cannot be mapped losslessly into the three EvidenceItem source types. The adapter
therefore handles only explicit event/group/index/entity/runtime-state shapes. Unrecognized fields
remain intact in the original tool result shown to the model but are not silently converted into a
misleading EvidenceItem. Filter results expose only a returned event index, not unseen raw event
content. This preserves compatibility without claiming a uniform semantic representation that the
source data does not support.

This change was validated only with deterministic fixtures and mocked model responses. It proves
that accepted and rejected outputs are auditable and that registry values are faithful to the data
actually shown during a run. It does **not** prove that a cited item semantically supports a claim,
that a hypothesis is reasonable, or that diagnosis quality improves. Whether a new model experiment
is justified is a separate post-implementation decision; no DeepSeek or Daytona call was made here.

### Registry-backed real-model smoke

At implementation commit `f6ef86d`, one registry-backed Compact-DIRECT smoke was run on
`runtime_service_unready_001` with DeepSeek `deepseek-v4-pro`, temperature 0, thinking disabled,
the typed-reference nine-tool surface, and a six-tool-round cap. This was one case and one run—not
an A/B test or an accuracy claim. The initial compact evidence and every returned tool result were
converted to system-owned EvidenceItems; no unqueried runtime collection was preloaded. The model
returned claims, evidence gaps, next checks, and references only. The 9,731-byte task, system prompt,
and tool schema are bound to hashes in the trace.

The run produced an accepted final after 5 model turns, 4 tool rounds, and 9 tool calls. It used
56,950 input and 2,453 output tokens (59,403 total) in 116.18 seconds, with no tool or validation
errors. The 60-item final registry contains only compact evidence and data returned by the selected
log, discovery, service, cgroup, filter, and bounded-context calls. Every final reference resolves
to that registry; repeated service evidence keeps one stable ID while retaining both discovery and
inspection provenance.

Manual review separates structural validity from semantic usefulness:

- The readiness timeout, repeated not-ready errors, readiness-probe messages, app-visible failure,
  and final crash are accurately tied to compact-group evidence. The service fields `state=starting`,
  `ready=false`, `health=degraded`, `required_dependency=vendor_bridge`, and
  `dependency_ready=false` match the actual returned runtime record.
- The first observation is only partially supported: its state fields are factual, but “stuck” and
  “the direct reason” are interpretations and its references omit the app symptom. The second
  observation likewise turns `dependency_ready=false` into a causal “preventing” statement. Typed
  references did not make those sentences semantically entailed.
- The dependency hypothesis has a useful evidence basis, medium confidence, and explicit missing
  evidence, but “most likely root cause” is stronger than the project’s preferred primary-candidate
  language. The ThermalHAL hypothesis is clearly low confidence and explicitly says there is no
  causal link.
- The final preserves three material gaps: why `vendor_bridge` is not ready, whether ThermalHAL is
  related, and unknown KMessage/Logcat cross-source ordering. Its next checks are concrete and
  diagnostic rather than remediation, although the thermal investigation is lower-value and more
  speculative than the dependency/service checks.

Therefore this smoke answers only basic usability: a real model can copy system-generated IDs,
produce a schema-valid referenced diagnosis, preserve useful unknowns, and suggest next checks.
It does not show stable behavior, superiority to the previous contract, or semantic entailment.
Because observation/hypothesis boundaries still need human review and the run is a single synthetic
replay, the registry-backed path remains experimental rather than a demonstrated production
diagnosis feature. The complete redacted trace is
`experiments/runtime_service_unready_registry_backed_smoke_trace.jsonl`; historical traces and
benchmarks were not changed.

### Container-startup paired case suite

Four paired startup-failure cases now separate “evidence can distinguish a mechanism” from “the same
symptom remains underdetermined.” Each pair has a complete and deliberately limited input, with
model-visible `case.yaml`/logs/state separated from hidden `expected.yaml`. A missing observation is
represented as `not_collected`, never as an empty result that could be misread as component absence.

The mechanisms cover an OverlayFS upper/work mount mismatch, a local cgroup-v2 OOM kill verified by
before/after `memory.events.local`, an alive runtime waiting on an unready host bridge, and a zero
per-user user-namespace allocation limit. The first three are synthetic from documented interfaces;
the namespace pair adapts a public runc case. No Linux host mutation or complete container launch was
performed on the macOS development host, and every log/state/timestamp is explicitly generated or
adapted rather than production evidence.

Offline validation only proves replayability, stable provenance/group identity, evidence reachability,
and absence of the removed markers from limited inputs. It does not prove that an Agent will select
the right query or diagnosis. The catalog records, per pair, competing explanations, supported
conclusion depth, unsupported exclusions, remaining unknowns, and the highest-information next check:
see [container-startup-case-catalog.md](container-startup-case-catalog.md).

### Paired container-startup model evaluation

The frozen registry-backed batch ran each complete/limited startup input exactly once with DeepSeek
`deepseek-v4-pro`, temperature 0, thinking disabled, six tool rounds, and provider retries disabled.
All prompts, schemas, input files, and execution order were hashed before the first response. Across
eight independent runs, six finals were accepted, one returned prose instead of JSON, and one useful
dependency diagnosis was rejected because it mistyped a single evidence ID. The batch used 32 model
turns, 49 tool calls, 375,698 tokens, and 235.96 seconds in aggregate; these are descriptive numbers,
not an accuracy estimate.

The pairwise result is more informative than the accepted count. Complete mount evidence was actively
confirmed through upper/work mount-state queries. Complete dependency evidence was actively read and
narrowed to an unready listener while preserving the unknown internal readiness cause, though its
final reference was invalid. Complete OOM and namespace mechanisms were largely already visible in
initial compact logs, so they are initial-evidence hits rather than proof of active investigation.
The accepted limited mount, dependency, and OOM outputs retained material gaps, but the OOM wording
still overstated exit 137 as an OOM signature; limited namespace produced no final.

Manual review also caught safety differences that schema validation cannot: one accepted namespace
answer over-excluded security policy from a missing denial, and two complete answers suggested
remediation rather than only diagnostic checks. The most frequent engineering issue was entity
locator ergonomics—five runs issued at least one invalid mount/cgroup lookup. The next candidate
improvement is a consistent discovery/typed-locator contract, not another Android-specific diagnosis
rule. Full evidence IDs, per-run metrics, failures, and trace locations are in
[container-startup-paired-model-evaluation.md](container-startup-paired-model-evaluation.md).

### Cgroup typed-reference follow-up

The paired evaluation exposed an evidence-access contract mismatch rather than a new diagnosis
failure: logs and process inspection showed a cgroup path, while the cgroup tool required a hidden
replay dictionary key and discovery could not return cgroups. A model could therefore choose the
right observation but still fail to retrieve it.

The typed-reference path now treats cgroup as the same kind of discoverable identity as process,
service, and mount. Discovery returns a canonical cgroup reference plus its recorded path, and the
new by-reference inspection validates that reference before returning state. Process inspection may
also attach the same reference when its explicit `cgroup_path` exactly and uniquely matches the
inventory; it never guesses from process names, basenames, or path prefixes. Showing that reference
registers identity only. Cgroup state EvidenceItems are created only after the cgroup record is
actually queried, with provenance pointing to the returned field.

The old name-based Python primitive remains available for compatibility, frozen traces were not
rewritten, and missing, ambiguous, or uncollected associations remain explicit. Offline tests cover
both discovery and process-linked query chains, including registry-backed final references. This
change has not been tested with a real model and therefore does not establish better tool-selection
success, diagnosis quality, or token usage.

### Cgroup typed-reference real-model smoke

A single post-fix smoke used `startup_cgroup_oom_full` behind neutral incident ID
`incident-20261015-01`. It ran DeepSeek `deepseek-v4-pro` with temperature 0, thinking disabled,
six tool rounds, and provider retries disabled. This was one usability check, not a rerun of the
eight-case batch or a controlled before/after comparison.

The model first made one invalid call by putting `/runtime/startup-02` in `entity_id`. It then used
cgroup discovery, received the canonical `cgroup:runtime_init` reference, passed that reference
unchanged to `inspect_cgroup_state_by_ref`, and successfully read the path, memory limit/current/peak,
local OOM counters before and after, host available memory, and observation-window status. The
registry attached field-level provenance only after this inspection. The accepted final cited the
returned cgroup counter, limit, peak, and host-memory EvidenceItems, so this run did exercise the new
interface; it also shows that identity guessing was reduced by discovery but not eliminated.

The run took 7 model turns, 6 tool rounds, 7 calls, 78,208 tokens, and 33.83 seconds. Its diagnosis
combined PID/cgroup assignment, a local `oom_kill` counter increment, the KMessage kill record,
limit/peak values, and host-memory evidence rather than treating exit 137 alone as proof. However,
the final used stronger “root cause” and host-pressure exclusion language than the project should
claim, did not cite the returned path EvidenceItem for every path assertion, and recommended raising
the limit and retrying—a remediation action rather than a read-only diagnostic check. Format and
reference validation therefore succeeded, while semantic review remains qualified.

The redacted trace and frozen input/config hashes are under
`experiments/cgroup-typed-reference-smoke/`. This one synthetic replay demonstrates basic tool-chain
usability only; it does not establish tool-selection reliability, diagnosis accuracy, stability, or
token improvement. The original paired-evaluation traces and conclusions remain unchanged.

### Registry-output reliability hardening

Two paired-evaluation failures were interface failures rather than evidence-quality conclusions.
`startup-eval-03` returned an ordinary planning sentence with no tool call, and the executor treated
every no-tool response as a final JSON answer. `startup-eval-06` produced useful structured content
but mistyped one long SHA256-derived EvidenceItem ID at `claims[6].evidence_refs[0]`; strict reference
validation correctly rejected it.

The opt-in hardened registry path now assigns run-local handles (`E001`, `E002`, ...) when evidence
first becomes model-visible. Initial compact evidence and later tool evidence use the same handle
space. Repeated evidence reuses its handle and later evidence never renumbers earlier entries. The
model emits handles, while the system resolves them exactly to the unchanged canonical EvidenceItem
IDs before normal reference validation. Unknown handles are rejected at their claim/reference path;
there is no fuzzy matching, prefix repair, or silent substitution. Traces retain the model's handle
payload, the handle-to-canonical map, a canonical resolved payload when resolution succeeds, and the
original provenance-bearing registry.

The same opt-in mode adds one finalization fallback only when a no-tool response has no parseable
JSON. It preserves the original prose, appends a final-only instruction containing the live Pydantic
schema, and invokes the configured model once without bound diagnostic tools. Valid JSON skips this
call; schema or reference failures do not trigger it; a failed fallback is not retried; and tool
round limits are unchanged. The implementation uses an instruction-constrained call rather than
DeepSeek-specific `response_format`, because JSON mode has not been verified across the existing
generic LangChain model interface. Pydantic and handle validation remain authoritative.

Offline fake-model tests reproduce both failure mechanisms and verify stable handles, canonical
resolution, provenance preservation, one-shot finalization, token/turn accounting, tool prohibition,
strict failures, trace redaction, and default-path compatibility. The evaluation runner exposes this
behavior through `--reliability-hardening`; its default remains the historical canonical-ID path.
These tests prove deterministic interface behavior only. No paid model was called, so they do not
show that a real model will reliably finalize, use handles correctly, or improve diagnosis accuracy.

### Registry-output reliability real-model smoke

One strictly controlled DeepSeek run exercised the opt-in hardened path on
`startup_dependency_unready_full`, exposed to the model only as `incident-hardening-01`. The model
used the shared short-handle space for both initial compact evidence and queried runtime state, then
returned an accepted final with 27 references across 17 unique handles. Every handle resolved
exactly to a canonical EvidenceItem ID, every referenced item retained source provenance, and no
unknown reference was repaired or ignored.

The run took 6 model turns, 5 tool-calling rounds, 9 tool calls, 79,300 tokens, and 25.59 seconds. It
also made one invalid guessed cgroup-reference call, which the typed tool rejected; this did not
invalidate the later diagnosis. The final identified the observed readiness chain, used queried
HostBridge process/service state, separated the unproven internal HostBridge mechanism into a
medium-confidence hypothesis, retained that evidence gap, and proposed read-only follow-up checks.
Some wording such as “never reached ready” and the noise classification is broader than the sampled
observations strictly prove, so schema/reference acceptance is not treated as proof of semantic
grounding.

The model returned valid structured JSON directly, so the one-shot finalization fallback was not
triggered and was not verified against the real provider in this run; its bounded behavior remains
covered only by deterministic tests. Compared descriptively with the historical 52,849-token
reference-failure run on the same replay case, this run resolved all short handles but used 79,300
tokens. These separate single samples are not a controlled A/B and do not establish token, latency,
accuracy, or reliability improvement. The manifest and redacted trace are under
`experiments/reliability-hardening-smoke/`.

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
