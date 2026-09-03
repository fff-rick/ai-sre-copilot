# 阶段 4 验收记录

验收日期：2026-09-03。

## 判定

当前判定：**PASS**。

知识导入、PostgreSQL + pgvector 精确混合检索、证据治理、持久调查事件、SSE 重连和 React
证据工作台均通过确定性门禁。线上 embedding 模型质量尚未纳入本阶段结论；阶段 6 仍需在
扩展数据集上比较真实 embedding、Prompt 和模型配置。

## 已通过

- JSON catalog 可幂等导入 Runbook、服务说明和历史事故 Markdown，并拒绝目录逃逸、空文档
  与无效有效期。
- 文档按标题/段落裁剪到有界 chunk，内容哈希用于去重和版本替换。
- PostgreSQL `simple` 全文分支与 pgvector 精确余弦分支并行召回，使用 RRF `k=60` 合并；
  服务、环境、文档类型和有效期在 SQL 召回前过滤。
- 阶段 4 不建立 HNSW/IVFFlat；当前小语料优先保持精确召回，避免为未出现的性能问题牺牲质量。
- 知识检索是固定调查节点，失败转换为 `EvidenceGap`；检索内容作为不可信证据，不控制工具或图边。
- 工具与知识证据统一执行内容裁剪、哈希去重、可靠性分级和 Evidence ID 引用校验；模型只能引用
  实际进入有界上下文的 Evidence ID。
- 调查列表、快照、时间线和证据详情均来自持久存储。终态快照与 `investigation.finished` 事件在
  同一数据库事务写入，避免 UI 看到完成状态却读取到空报告。
- SSE 使用单调事件 ID、`Last-Event-ID` 重放、retry 提示和代理禁用缓冲；Web 先读取快照，再用
  SSE 触发刷新，断线或页面刷新不会取消调查或丢失最终结果。
- React 工作台展示调查列表、严重度、进度时间线、Top 根因、置信度和可点击 Evidence ID；证据
  抽屉展示原始查询、来源、片段、可靠性和内容哈希。

## 检索基线

数据集：`stage4-retrieval-v1`；4 份文档，8 个查询。

| 范围 | 用例 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|---:|
| 总体 | 8 | 0.750 | 0.875 | 1.000 | 0.844 |
| English | 7 | 0.857 | 1.000 | 1.000 | 0.929 |
| 中文 | 1 | 0.000 | 0.000 | 1.000 | 0.250 |

该基线使用 64 维确定性 feature-hash embedding，目的是验证流水线、过滤和排序的可复现性，
不代表生产语义模型效果。中文查询在第 4 位才命中预期 Runbook，证明 `simple` 配置不适合直接
宣称中文检索可用。当前更合理的下一步是扩充中文用例并比较分词/embedding，而不是立即部署
独立搜索集群。

## 自动复现

```bash
make acceptance-stage4
```

门禁执行：

- Python：42 个测试通过，总覆盖率 92.82%。
- Web：5 个测试通过，行覆盖率 91.66%，分支覆盖率 88.09%。
- Go Tool Gateway 与 Testbed：`go test -race ./...` 通过。
- Python Ruff/Mypy、Go Vet、Web ESLint/Prettier/TypeScript 均通过。
- 实际 PostgreSQL 18 + pgvector 0.8.6 导入、混合查询、过滤与跨仓储实例事件重放通过。
- Python/Go/Testbed/Web 构建和两个 Compose 模型校验通过。

检索 JSON/Markdown 运行产物写入忽略提交的 `artifacts/stage4-retrieval.*`。

## 架构评审结论

阶段 4 继续使用现有 PostgreSQL，而不增加 Elasticsearch/OpenSearch、专用向量数据库、Redis、
Kafka 或前端全局状态框架。它们会增加部署与一致性成本，却没有当前规模下的测量收益。详细
决策见 [ADR 0006](adr/0006-stage4-pg-hybrid-retrieval-and-durable-events.md)。

## 适用边界

- 当前 corpus 仅用于工程演示，不是完整企业知识库。
- 中文词法 Recall@3 未达可用水平，必须扩充数据集后再决定中文分词方案。
- 精确向量扫描适合当前小语料；只有 P95 延迟达到 ADR 阈值后才评估近似索引。
- 阶段 4 仍只生成 `ProposedAction`；批准、修改、拒绝和隔离变更属于阶段 5。
