# ADR 0006：阶段 4 使用 PostgreSQL 精确混合检索与持久事件流

- 状态：Accepted
- 日期：2026-09-03

## 背景

阶段 4 需要导入 Runbook、服务说明和历史事故，提供关键词与语义混合检索，
并让 Web 在刷新或 SSE 断线后恢复调查时间线。V1 语料规模有限，现有部署已经包含
PostgreSQL 18、pgvector 0.8.6 和 Investigation Service。

## 判断

有条件推荐继续使用 PostgreSQL + pgvector。它适合当前单节点、小语料和强元数据过滤场景，
但中文全文检索质量必须通过独立数据集验证；如果固定语料上的 Recall@K 不达标，再评估
专用中文分词或独立搜索服务，不能仅凭架构直觉增加组件。

## 决策

1. 文档按标题和有界段落切片；规范化内容哈希作为幂等导入与去重键。
2. 关键词分支使用 PostgreSQL `simple` 全文检索，语义分支使用 pgvector 余弦距离。
3. 两个分支各自召回候选后使用加权 Reciprocal Rank Fusion（RRF）合并。
4. 阶段 4 使用精确向量检索，不创建 HNSW/IVFFlat 索引。近似索引只有在语料量和查询延迟
   达到已测量阈值后才引入，并且必须与精确检索对比召回率。
5. 嵌入能力放在厂商无关端口后；线上使用 OpenAI-compatible `/embeddings`，测试使用确定性
   Fake，不让领域或检索代码依赖模型 SDK。
6. 服务、环境、文档类型、版本和有效期在 SQL 召回前过滤，不在应用层事后过滤。
7. 调查快照和事件写入 PostgreSQL。每个事件具有单调 ID；SSE 接受 `Last-Event-ID` 并重放
   后续事件。SSE 是可丢弃连接，不是任务执行或状态存储边界。
8. Web 首先读取持久快照，再订阅事件；收到事件后重新拉取快照。这样页面刷新、代理超时或
   SSE 重连都不会丢失最终结果。

## 被否决的方案

- **Elasticsearch/OpenSearch 或独立向量数据库**：会增加部署、备份和一致性成本，当前规模没有
  可证明收益。
- **近似向量索引作为默认值**：阶段 4 的验收目标是检索召回，先牺牲召回换取未被证明需要的
  性能不合理。
- **将完整调查状态只放在 SSE 内存队列**：进程重启、断线和页面刷新会丢事件，违反 AT-R05。
- **引入 Kafka/Redis 管理 UI 事件**：PostgreSQL 事件表已能满足当前并发和保留需求。

## Trade-off

精确向量扫描随语料增长会变慢，`simple` 全文配置对无空格中文的词法质量有限；换来的收益是
更少组件、确定的召回基线、事务一致性和简单恢复。阶段 4 的 Recall@K 报告必须按文档类型和
语言暴露失败，不能用总体平均掩盖中文检索不足。

## 演进触发条件

- 固定评测集 Recall@5 低于阶段门槛：先改进切片、查询扩展或中文分词。
- 精确向量检索 P95 超过 200 ms 且语料规模可复现：比较 HNSW 与精确检索的延迟/召回。
- PostgreSQL 全文检索在中文和复杂过滤上持续不达标：再评估专用搜索服务。

## 参考

- PostgreSQL 18 Full Text Search：<https://www.postgresql.org/docs/current/textsearch.html>
- pgvector Hybrid Search 与索引说明：<https://github.com/pgvector/pgvector>
- SSE `id`、`retry` 与重连语义：<https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events>
