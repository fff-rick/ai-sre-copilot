# Tool Gateway

Go 服务是可信工具执行边界。阶段 0 仅提供健康检查、只读工具接口和 Fake Registry，不接受任意命令，也不包含任何 LLM 逻辑。

```bash
go test -race ./...
go run ./cmd/server
```

