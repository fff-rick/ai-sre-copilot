# Tool Gateway

Go 服务是可信工具执行边界。它提供八个固定只读工具，以及阶段 5 新增的三种审批受控 Deployment 变更；所有变更都会重新校验 PostgreSQL 审批、参数哈希、有效期、角色、隔离 namespace 和幂等键。不接受任意命令、任意数据源 URL 或动态工具注册，也不包含 LLM 逻辑。

```bash
go test -race ./...
GATEWAY_AUTH_TOKEN=local-development-token go run ./cmd/server
```

- HTTP 健康检查：`:8081`
- gRPC Tool Gateway V1：`:9091`
- 数据源地址、Git 仓库和 kubeconfig 只允许在进程启动时配置。
- 变更要求 `DATABASE_URL` 和 `MUTATION_ALLOWED_NAMESPACE`；未配置时只读能力保留、变更 fail closed。
- 本地 Compose 使用明文 HTTP/2；真实跨主机部署必须在基础设施层启用 mTLS。
