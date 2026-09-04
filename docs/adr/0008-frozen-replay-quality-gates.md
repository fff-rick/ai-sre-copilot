# ADR 0008：冻结回放与代码评分作为阶段 6 主门禁

- 状态：Accepted
- 日期：2026-09-04

## 背景

阶段 6 需要在不少于 30 个故障用例上比较 Prompt 或模型配置，并量化根因排名、证据引用、
安全、延迟和成本。门禁必须能在没有真实模型凭据、外部评测平台和在线观测后端的 PR 环境
中复现，同时每个失败用例必须能定位到 Trace、检查点和工具记录。

## 方案判断

采用仓库内版本化数据集、内容哈希冻结的脱敏工具记录、确定性代码评分和 JSON/Markdown
报告作为 CI 主门禁；OpenAI-compatible 在线评测使用同一数据集、评分器和双 Prompt 配置，
但不进入无凭据的普通 PR。

不把脚本化回放模型的结果解释为真实模型质量。回放只证明工作流、指标计算、安全阻断和
回归门禁是确定的；发布候选仍必须单独运行真实模型评测并记录实际模型标识和价格参数。

## 候选方案

### 自建轻量回放与评分（采用）

- 复杂度：只增加两个 Python 模块、一个冻结数据集和一个 CLI，沿用 Pydantic、现有工作流与
  本地 Artifact 边界。
- 可靠性：内容哈希检测数据漂移；请求必须精确命中录制参数；安全用例失败直接返回非零。
- 可维护性：失败分类和 V1 门槛由项目领域模型表达，不依赖通用 Judge 的隐含 Prompt。
- 局限：没有托管实验 UI，也不能替代真实模型的多次抽样和人工复核。

### LangSmith

LangSmith 原生支持离线数据集、实验、代码/LLM 评估器、汇总与配置对比，能力完整。但将它
作为唯一门禁会增加外部服务、凭据和数据上传依赖；本项目的冻结工具证据与审批安全断言仍需
自定义评估器。因此阶段 6 不引入，待团队需要共享实验 UI 和线上抽样时再评估。

### DeepEval

DeepEval 提供面向 Agent Trace、工具正确性和任务完成度的评测指标，适合开放式模型质量研究。
当前 V1 的门槛已经有明确、可代码计算的 Ground Truth，引入 LLM Judge 会增加成本、非确定性
和新的模型依赖。发布后若需要评估难以规则化的语义质量，可作为补充而非安全门禁。

## 数据与执行设计

1. `stage6-faults-v1` 使用 8 个故障模板和 4 个服务名、时间窗口、噪声变体，展开为 32 个
   唯一用例；数据集整体由 SHA-256 冻结。
2. 录制装饰器递归脱敏敏感字段和常见令牌形态，再对完整请求/响应包计算 SHA-256。
3. 回放客户端只接受工具名和参数都匹配的请求，未录制调用返回稳定 `REPLAY_MISS`。
4. 同一批录制响应分别运行 `hypotheses-v2` 与 `evidence-first-v3`，隔离工具变化对 Prompt
   对比的干扰。
5. 报告同时输出总体和故障族指标，并为每个用例记录 Trace ID、最终检查点路径和录制哈希。
6. 安全强制用例要求不可信日志不能扩展固定只读工具集或生成可执行变更；任一失败使 CLI
   返回非零。

## 后果与演进条件

- PR 可完全离线复现 32 用例质量门禁，且不需要上传工具数据。
- 本地 Artifact 适合当前单仓库规模；只有当实验协作、权限和历史查询成为真实瓶颈时，才将
  报告元数据写入 PostgreSQL 或引入托管评测平台。
- 回放模型是测试替身。阶段 7 发布报告必须补充真实模型、多次运行、实际 Token/价格和人工
  复核，不能引用回放准确率宣称线上效果。

## 参考

- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)
- [LangSmith Evaluation Types](https://docs.langchain.com/langsmith/evaluation-types)
- [DeepEval agent evaluation metrics](https://github.com/confident-ai/deepeval/blob/main/docs/content/guides/guides-ai-agent-evaluation-metrics.mdx)
