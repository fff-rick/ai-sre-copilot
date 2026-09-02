# 技术栈与选型

## 1. 选型结论

V1 采用“Python 智能面 + Go 可信工具面 + TypeScript 交互面”的多语言架构。多语言不是为了增加技术数量，而是为了建立清晰职责：Python 负责非确定性的 AI 调查，Go 负责高权限工具访问和确定性安全控制，TypeScript 负责人机协作界面。

## 2. 技术栈

| 层次 | 技术 | 用途 |
|---|---|---|
| Web | React、TypeScript、Vite | 调查时间线、证据、审批、Trace 与报告界面 |
| AI API | Python、FastAPI、Pydantic | HTTP/SSE API、领域模型、结构化输出与输入校验 |
| Agent 编排 | LangGraph | 显式状态图、检查点、暂停恢复、人工审批 |
| LLM 接入 | 厂商无关适配层、OpenAI-compatible API | 模型调用、工具调用、流式输出 |
| 分析 | NumPy/Pandas；按阶段引入 Drain3、ruptures 或 scikit-learn | 时间序列与日志辅助分析，不替代确定性监控规则 |
| 工具网关 | Go | 工具注册、并发、限流、超时、RBAC、审批与审计 |
| 内部协议 | Protobuf、gRPC | Python/Go 强类型通信、版本控制和超时传播 |
| 关系数据 | PostgreSQL | 调查、步骤、证据、审批、审计、评测结果 |
| 向量检索 | pgvector | Runbook 与历史事故语义检索 |
| 对象存储 | 本地文件系统；需要时升级 MinIO/S3 | 大体积原始日志包和评测产物 |
| 可观测性 | OpenTelemetry Collector | 统一采集 Trace、Metric 与 Log |
| 监控 | Prometheus | 指标与告警源 |
| 日志 | Loki | 日志源 |
| 链路 | Tempo | Trace 源与项目自身 Trace 后端 |
| 展示 | Grafana | 测试环境可观测数据浏览 |
| 测试环境 | Docker Compose | V1 可重复部署和故障注入 |
| 集群环境 | Kubernetes/kind | V1 后期验证 K8s 工具，非首阶段依赖 |
| 工具链 | `uv`、Go Modules、`pnpm` | Python、Go、前端依赖管理 |

具体版本在首次脚手架阶段锁定，并由依赖更新工具维护；文档不写浮动的“latest”版本。

## 3. 关键选型比较

### 3.1 Python、Go 还是多语言

| 方案 | 复杂度 | AI 生态 | 工具执行可靠性 | 求职展示 | 判断 |
|---|---:|---:|---:|---:|---|
| 全 Python | 低 | 高 | 中 | 高 | 适合快速原型，但安全边界较弱 |
| 全 Go | 低 | 中 | 高 | 中高 | 可实现，但 AI/数据实验成本偏高 |
| Python + Go | 中 | 高 | 高 | 高 | 推荐，前提是严格按边界拆分 |

最终选择 Python + Go。禁止在两种语言中重复实现同一业务逻辑；领域状态归 Python，权限和实际工具执行归 Go。

### 3.2 LangGraph、手写状态机还是 Temporal

| 方案 | 优点 | 风险 | 适用判断 |
|---|---|---|---|
| 手写状态机 | 依赖少、完全可控 | 暂停恢复、检查点和回放工作量大 | 可作为最小基线或单元测试执行器 |
| LangGraph | 显式图、持久化、HITL、流式和恢复能力直接匹配 | 框架耦合；持久状态需考虑版本兼容 | V1 推荐 |
| Temporal | 跨服务长任务的持久执行和恢复能力强 | 部署和认知成本高；与 LangGraph 职责重叠 | V1 不采用，规模扩大后评估 |

LangGraph 状态必须使用项目自己的 Pydantic/TypedDict 类型，模型调用和工具调用放在适配器之后，避免业务代码直接耦合 LangChain 组件。官方文档说明检查点可支持暂停审批、状态查看和故障恢复，因此它解决的是本项目的真实需求，而不是装饰性依赖。

### 3.3 gRPC 还是 MCP

| 协议 | 主要价值 | 本项目用途 |
|---|---|---|
| gRPC | 内部强类型 RPC、版本化、超时、状态码和代码生成 | Python 与 Go 的内部协议，V1 采用 |
| MCP | Agent 与开放工具生态之间的发现和标准化调用 | 后续作为 Go 网关的可选外部适配层 |

V1 工具集合固定，内部通信更需要类型和错误语义，因此不以 MCP 代替内部 RPC。MCP 适配器不能绕过网关权限和审计逻辑。

### 3.4 PostgreSQL + pgvector 还是独立向量数据库

V1 语料是 Runbook 和历史事故，数量预计远低于需要分布式向量数据库的规模。PostgreSQL 能同时保存领域数据、全文字段、向量和事务记录，因此优先使用 PostgreSQL + pgvector。

只有在以下情况出现后才重新评估 OpenSearch、Qdrant 或其他检索服务：

- 中文或复杂过滤下的检索质量无法达标。
- 数据量和吞吐超过单 PostgreSQL 节点的可接受范围。
- 需要独立检索团队、索引生命周期或多集群隔离。

### 3.5 Docker Compose 还是 Kubernetes

V1 的首要目标是可重复故障和自动验收，而不是展示集群运维。早期使用 Docker Compose 可降低环境成本；当只读调查闭环稳定后，再使用 kind 验证 Kubernetes Event、Deployment、Pod 和后续隔离变更工具。

## 4. 明确不引入的组件

### Kafka

V1 没有需要大规模事件回放和多消费者解耦的吞吐需求。告警 Webhook 和数据库任务状态足够。

### Redis

V1 不将 Redis 作为必选项。只有出现明确的分布式锁、短期缓存或限流需求时才增加；LangGraph 检查点使用 PostgreSQL。

### Elasticsearch/OpenSearch

V1 直接查询 Loki，知识检索使用 PostgreSQL。避免维护第二套搜索集群。

### 多 Agent 框架

指标、日志和 Trace 的并行查询首先使用普通异步任务。只有评测证明单一上下文限制准确率，且调查分支具备独立目标时，才引入子 Agent。

## 5. 开发规范

- 所有模型返回的领域对象必须经过 Pydantic Schema 校验。
- Protobuf 变更必须保持向后兼容并通过 Breaking Change 检查。
- Python 使用静态类型检查和格式化；Go 使用 `gofmt`、`go vet` 和测试；前端启用严格 TypeScript。
- 所有外部依赖必须设置连接、请求和总执行超时。
- 测试不得依赖真实付费模型；核心流程使用 Fake Model 和录制工具响应。
- 在线模型评测单独执行并记录模型标识、参数、日期和成本。

