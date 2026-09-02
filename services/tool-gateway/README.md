# Tool Gateway

Go 服务是可信工具执行边界。阶段 2 提供版本化 gRPC 契约、八个固定只读工具、调用者认证与 RBAC、deadline、按调用者限流、审计、脱敏和受控 Artifact 落盘；不接受任意命令、任意数据源 URL 或动态工具注册，也不包含 LLM 逻辑。

```bash
go test -race ./...
GATEWAY_AUTH_TOKEN=local-development-token go run ./cmd/server
```

- HTTP 健康检查：`:8081`
- gRPC Tool Gateway V1：`:9091`
- 数据源地址、Git 仓库和 kubeconfig 只允许在进程启动时配置。
- 本地 Compose 使用明文 HTTP/2；真实跨主机部署必须在基础设施层启用 mTLS。
