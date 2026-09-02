# ADR-0004：固定只读工具 gRPC 信任边界

- 状态：Accepted
- 日期：2026-09-02

## 判断

阶段 2 使用版本化 `ToolGatewayV1` gRPC 服务，为 Prometheus、Loki、Tempo、发布记录、Git 和 Kubernetes 提供八个独立只读 RPC。每个 RPC 使用独立参数消息；数据源地址、仓库路径和 kubeconfig 只能在进程启动时配置。Python 调查平面只持有生成客户端和网关服务凭据，不持有数据源凭据。

gRPC 调用必须携带 deadline、Bearer 服务凭据、调查 ID、Trace ID 和调用者声明。网关执行角色校验、按 actor 限流、参数哈希审计、递归脱敏及响应大小控制；超过内联上限的脱敏结果保存为网关管理的 Artifact。错误同时使用标准 gRPC 状态码和 `ToolError` 详情。

## 比较

| 方案 | 类型安全 | 攻击面 | 跨语言维护 | 决策 |
| --- | ---: | ---: | ---: | --- |
| 单一 `Execute(name, Struct)` 动态工具 | 低 | 高 | 中 | 不采用；名称和参数容易绕过静态审查 |
| HTTP + 自定义 JSON 错误 | 中 | 中 | 中 | 不采用；客户端、deadline 和错误语义需重复实现 |
| MCP 工具服务器 | 中 | 中 | 高 | 暂不采用；V1 没有第三方工具生态需求 |
| 八个固定 Protobuf/gRPC RPC | 高 | 低 | 低 | 采用 |

连接器实现使用官方 client-go 访问 Kubernetes，并用 typed fake client 执行常规契约测试；阶段验收使用临时 kind API Server。Git 使用纯 Go 只读库，不启动 Shell 或 Git 子进程。可观测后端只允许启动配置中的固定 base URL，请求只能提交相应查询语言、时间窗和数量上限。

## 约束

- V1 服务不暴露 Shell、SQL、任意 URL、插件上传或动态注册入口。
- PromQL、LogQL 和 TraceQL 是受限数据源查询语言，不得被复用为任意代码执行通道。
- 所有出站请求继承 gRPC context，客户端必须设置 deadline。
- 网关日志不记录原始参数、上游响应或认证凭据，只记录参数哈希和安全错误分类。
- 本地 Compose 的 gRPC 使用明文 HTTP/2，只允许在本机/内部网络使用；跨主机部署必须增加 mTLS 和可验证的工作负载身份。
- 当前 Bearer 凭据认证 Python 服务，actor 是该受信服务传播的声明；若未来允许多个直接调用方，必须将 actor 绑定到 mTLS/JWT 身份，不能继续共享服务令牌。
- kind 是阶段验收门禁而非每次提交门禁，避免每个 PR 重复拉取大型节点镜像；Fake client-go 和并发测试仍在每次提交运行。

## 后果

独立 RPC 增加少量生成代码，但让接口差异、兼容性和权限审查可见。client-go 依赖体积较大，但避免自行实现 kubeconfig、TLS、认证和 API 类型；这项成本仅位于 Go 网关。后续新增工具必须修改 Protobuf、生成客户端和契约测试，不能通过运行时配置绕过评审。
