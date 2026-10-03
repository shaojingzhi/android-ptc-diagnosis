# Project Goal

这是一个以求职展示为优先的小而精 Android/container 日志诊断项目。

核心问题是：从高噪声 Logcat / KMessage 中识别 primary failure candidates、cascading errors 和无关 noise，再把压缩后的 evidence 交给 LLM 分析。

项目必须：

- 能通过真实 ADB 链路运行；
- 能通过 Replay dataset 可重复评测；
- 能清楚比较 RAW / FIXED / DIRECT / PTC / HYBRID；
- 能清楚说明个人 contribution；
- 优先可理解性、可维护性和实验可信度，而不是框架完整度。

# Core Contribution

本项目自己的主要 contribution：

- Android/container 真实诊断 workload 抽象；
- primary failure / cascading error log triage；
- Direct / PTC / Hybrid execution strategy；
- trace、evaluation 和 badcase-driven iteration。

以下能力优先复用成熟项目，不重复造轮子：ADB/MCP infrastructure、通用 PTC sandbox/runtime、通用日志聚类算法和通用 Agent framework。

# Reuse Strategy

优先通过 dependency / adapter 接入：

- Android-Debug-Bridge-MCP：真实 ADB / Android tool layer；
- open-ptc-agent：PTC runtime；
- Drain3：日志模板聚类与重复日志压缩；
- PokeBench：evaluation methodology 参考；
- llm-anr：evidence-first Android 日志分析方法参考。

不要 fork 或复制大量上游源码。

# Design Constraints

- 保持代码小而精；核心自研代码目标约 1500–2500 行，case/test 数据不计；
- 一个简单模块能解决的问题不要拆出多个 abstraction；单个源码文件原则上尽量不超过约 250 行；
- 不为了未来可能需求提前设计；不做无证据的重构；不因“架构更优雅”增加复杂度；
- 不实现 Multi-Agent、Memory、RAG、UI、通用 workflow engine、自研 MCP framework、自研通用 sandbox、复杂 causal graph、严格 causal inference engine、自动 remediation、大型部署体系或与当前 milestone 无关的新能力。

执行模式必须保持清晰区分：

- RAW：原始 Logcat / KMessage 直接交给 LLM；
- FIXED：固定 Python triage pipeline 后交给 LLM；
- DIRECT：传统 Agent Tool Calling，LLM 每轮决定下一次工具调用；
- PTC：模型生成程序，在一次程序执行中组合有限的 filter/join/group/rank 与工具调用；
- HYBRID：PTC 批量处理，必要时进行有限的 Direct Tool Calling 补充。

PTC 的重点是动态组合 primitives、避免为每类故障手写 pipeline，并减少 Direct 的 round-trip 和中间日志 context；不要求证明比 FIXED 更快。

# Causal Language

项目不声称从日志中证明严格因果关系。使用 primary failure candidate、cascading error、propagation candidate、supporting evidence 等表述，不声称系统已证明真实 causality。

# Milestones

- M0：Replay + normalized log foundation
- M1：Real ADB E2E（必做）
- M2：Fixed triage + Drain3
- M3：PTC integration
- M4：Evaluation / Hybrid based on actual badcases

Real ADB E2E 是项目必做项；完成每个 milestone 后先评估结果，再决定是否继续。不得自行提前实现后续 milestone。

# Change Rule

每次准备新增模块、依赖或 abstraction 前，先判断：

1. 是否是当前 milestone 必需？
2. 是否改善真实可运行性、评测可信度或面试表达？
3. 是否可以复用已有实现？
4. 是否会增加用户理解和维护成本？

如果前三项不能明确回答“是”，或者明显增加复杂度，则不要实现。
