# 阶段 2 验收记录

验收日期：2026-09-02。

## 验收范围

本轮覆盖版本化 Protobuf/gRPC 协议、Go 工具网关、Python 生成客户端、Prometheus/Loki/Tempo/发布记录/Git/Kubernetes 连接器，以及认证、RBAC、deadline、限流、响应大小、Artifact、审计和脱敏边界。

## 结果

阶段 2 的四个退出条件全部满足：

1. 八个固定只读工具均通过同一生成契约的 RPC 测试。
2. Python 客户端可调用 Go 网关，并传播调查 ID、Trace ID、调用者、Bearer 凭据和 deadline。
3. 超时、权限失败、限流和数据源不可用映射为稳定 gRPC 状态码与 `ToolError` 详情。
4. 网关不存在 Shell、任意 URL、动态工具注册或通用代码执行 RPC。

| 工具 | 契约 | 实际数据源验收 |
| --- | --- | --- |
| `prometheus.query` | 通过 | Stage 1 Prometheus |
| `loki.query_range` | 通过 | Stage 1 Loki |
| `tempo.get_trace` | 通过 | Stage 1 Tempo |
| `tempo.search_traces` | 通过 | Stage 1 Tempo |
| `releases.list` | 通过 | Stage 1 JSONL 事件记录 |
| `git.get_commit` | 通过 | 当前只读 Git 仓库 |
| `kubernetes.get_workload` | 通过 | client-go Fake + 临时 kind API Server |
| `kubernetes.list_events` | 通过 | client-go Fake + 临时 kind API Server |

## 自动复现

```bash
make proto-check
make test
make lint
make build
make compose-config
make testbed-up
make testbed-smoke
make test-integration
```

`scripts/verify-stage2.sh` 启动临时 Go 网关，通过 Python 生成客户端并行访问六类 Stage 1/Git 实际数据源。Kubernetes 实集群验收使用 kind 创建零副本 Deployment 与测试 Event，验证完成后删除临时集群。常规 CI 使用 client-go typed fake，避免每次提交下载大型节点镜像。

## 安全与可靠性证据

- 64 路并发 RPC 在 Go race detector 下通过。
- 无凭据、越权角色、超出限流、上游不可用和 deadline 到期均在 RPC 边界失败。
- 上游错误原文不返回客户端；审计只写参数 SHA-256，不写查询和响应原文。
- JSON key 与 Bearer 模式在返回 Python/模型上下文前递归脱敏。
- 内联结果超过 64 KiB 后转为内容寻址 Artifact，原始最大响应限制为 4 MiB。
- Protobuf 生成漂移由 CI 阻断；字段删除必须保留字段号与名称。

## 适用边界

- 本次验证的是调查所需只读能力，不包含审批或变更执行；变更工具属于阶段 5 的独立协议。
- 本地 gRPC 为明文内部连接，不代表跨主机生产安全配置。
- kind 只验证 Kubernetes API、认证和 typed client 行为，不代表生产集群规模或 RBAC 设计完成。
- 性能结果用于发现竞态和无界等待，不作为生产容量声明。
