# ADR-0005：有界调查工作流与严格结构化输出

- 状态：Accepted
- 日期：2026-09-02

## 背景

阶段 3 需要可恢复地执行告警范围判定、多源证据收集、根因候选、验证和报告。模型输出与日志内容均不可信；任一数据源或模型调用失败不得制造无限循环，也不能让模型选择任意工具。调查通常在数分钟内完成，V1 以单实例或低并发 Compose 环境为主。

首次真实模型验收暴露了一个 Fake Model 无法发现的契约缺口：`json_object` 只能约束为合法 JSON，模型没有收到字段级 Schema，导致五个用例都在本地结构校验后降级为空候选。

## 决策

1. 使用 LangGraph Graph API 表达一个固定单 Agent 状态图，节点和条件边由代码定义。
2. 使用 PostgreSQL Checkpointer 保存节点级状态；另设 `investigations` 表负责查询、取消、有限尝试和带心跳的短租约认领。
3. Checkpoint 只保存 JSON 兼容值，并禁止 msgpack 构造任意 Python 模块对象。
4. 供应方请求使用 `response_format.type=json_schema`、`strict=true` 和全字段必填 Schema；本地仍用 Pydantic 校验排名、范围和 Evidence ID。
5. `hypothesis_id` 与 `verification_status` 由确定性代码产生，不交给模型。
6. 单证据源失败转换为 Evidence Gap；模型错误有限重试，预算耗尽后基于已有证据报告。

## 备选方案

| 方案 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- |
| 手写异步状态机 | 依赖少 | 需要自行实现 checkpoint、恢复和中断语义 | 不选 |
| Temporal | 长事务和多实例恢复成熟 | 增加服务、运维和学习成本，当前调查时长与规模不足以证明必要性 | 不选 |
| ReAct 或多 Agent | 工具选择更灵活 | 路径不确定、预算和安全边界更难验证 | 不选 |
| 仅 Prompt + `json_object` | 兼容面广 | 只保证 JSON 语法，真实验收已证明字段契约不可靠 | 仅作无 Schema 兼容路径 |
| 强制 function call | 同样可约束参数 | 本场景没有实际函数调用语义，会增加解析分支 | 不选 |
| 迁移 Responses API | 新能力与统一输出接口 | 阶段 3 不需要 hosted tools 或多模态，且会缩小 OpenAI-compatible 供应方兼容面 | 暂不迁移 |

## 后果

- 生产模型必须支持 Chat Completions Structured Outputs；不支持时应显式配置兼容策略，不能静默降低发布门禁。
- Schema 变化需要同时更新 Prompt 版本和离线/在线数据集结果。
- PostgreSQL worker 适合 V1；只有真实多实例争抢、跨天任务或更复杂补偿需求出现后才重新评估 Temporal 或专用队列。

## 依据

- [OpenAI Structured Outputs API reference](https://platform.openai.com/docs/api-reference/chat/create#chat-create-response_format)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
