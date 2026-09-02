# 项目立项背景

## 1. 项目名称

- 中文名称：AI-SRE 智能故障调查与安全处置 Copilot
- 英文名称：AI-SRE Copilot
- 仓库名称：`ai-sre-copilot`
- 文档版本：v0.1
- 立项日期：2026-09-02

## 2. 背景

生产事故发生后，值班工程师通常需要在告警平台、指标系统、日志系统、分布式链路、Kubernetes、代码仓库、发布平台和历史复盘之间反复切换。真正耗时的工作并不只是“看懂一条日志”，而是完成以下信息闭环：

1. 判断告警是否真实以及影响范围。
2. 建立故障发生前后的时间线。
3. 将异常指标与日志、Trace、依赖拓扑和最近变更相关联。
4. 提出并验证多个根因假设。
5. 在风险可控的前提下选择处置方案。
6. 验证恢复结果并沉淀事故知识。

传统告警规则擅长检测已知模式，但难以完成跨数据源的语义关联。通用聊天模型能够解释文本，却无法直接访问实时系统，也缺少权限、证据、审计、恢复和评测边界。AI Agent 适合承担“收集信息—形成假设—调用工具验证—生成建议”的调查工作，但不能未经约束地操作生产系统。

Google SRE 已公开介绍使用 AI 生成事故专属调查视图、关联历史事故与 Runbook、形成并验证根因假设，并在关键处置上保留人工审核的实践。这说明 AI-SRE 的合理切入点不是完全自主运维，而是证据驱动的调查和分级自治。

## 3. 现有方案的不足

### 3.1 纯告警规则

- 优点：确定、快速、成本低。
- 不足：只能覆盖预先编码的模式，难以解释跨服务连锁故障。
- 本项目定位：保留规则作为确定性信号，不用 LLM 替代规则引擎。

### 3.2 日志问答或通用 RAG

- 优点：开发快，适合知识查询。
- 不足：通常只回答用户问题，不能主动规划调查、调用多源工具或验证假设。
- 本项目定位：RAG 只用于检索 Runbook、服务说明和历史事故，不作为完整产品。

### 3.3 无约束自治 Agent

- 优点：演示效果强，能完成开放式任务。
- 不足：结果不可预测，可能循环调用、越权操作、放大事故或产生无法追责的变更。
- 本项目定位：使用显式工作流、最小权限、审批令牌、幂等和审计约束 Agent。

### 3.4 直接采购成熟 AIOps 产品

- 优点：连接器、运维支持和企业能力完整。
- 不足：不适合作为个人工程能力展示，也难以深入理解 Agent 调查链路和评测方法。
- 本项目定位：不追求替代商业平台，而是实现一个范围受控、可验证、可开源演示的垂直系统。

## 4. 目标用户

### 4.1 主要用户

- 中小团队的 SRE、DevOps、后端工程师和 DBA。
- 需要值班但尚未建设完整 AIOps 平台的研发团队。
- 希望评估 AI Agent 在运维场景中可靠性的技术负责人。

### 4.2 用户痛点

- 告警上下文分散，首次响应时间长。
- 故障排查高度依赖少数资深工程师。
- 历史事故和 Runbook 难以在告警时准确复用。
- 处置过程缺少统一证据链和审计记录。
- AI 方案“看起来会回答”，但无法证明真实任务成功率。

## 5. 核心使用场景

1. 告警分诊：判断影响服务、严重程度和用户影响。
2. 故障调查：并行查询指标、日志、Trace、Kubernetes 事件和最近发布。
3. 根因分析：生成候选假设并通过工具验证或否定。
4. 处置建议：根据证据和 Runbook 给出风险分级的操作建议。
5. 人工审批：对重启、扩缩容、回滚等操作执行批准、修改或拒绝。
6. 恢复验证：在处置后重新查询关键 SLI 和告警状态。
7. 事故复盘：自动生成带时间线、证据和改进项的复盘草稿。
8. 离线评测：使用故障数据集回放不同模型、Prompt、检索和工具版本。

## 6. 项目机会

该项目能同时验证以下 AI 应用开发能力：

- 将真实业务流程建模为受控 Agent 工作流。
- 设计跨语言服务边界和强类型工具协议。
- 组合 LLM、规则、统计分析、RAG 和领域知识。
- 处理权限、提示注入、危险操作和人在回路。
- 建立 Agent Trace、数据集、回放与自动回归体系。
- 用任务成功率、证据质量、延迟和成本证明效果。

## 7. 非目标

V1 明确不做以下事项：

- 不接入真实生产集群执行自动变更。
- 不建设通用聊天助手或万能 Agent 平台。
- 不使用多 Agent 作为默认架构。
- 不训练基础大模型。
- 不替代 Prometheus、Loki、Tempo、Grafana 等可观测系统。
- 不建设复杂 CMDB；V1 使用测试环境的服务目录和依赖关系。
- 不保证对未知故障给出唯一正确根因；系统输出候选假设和证据。

## 8. 参考资料

- [Google SRE：AI Engineering for Reliable Operations](https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/)
- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenTelemetry Generative AI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)
- [Kubernetes Client Libraries](https://kubernetes.io/docs/reference/using-api/client-libraries/)

