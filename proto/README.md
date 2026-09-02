# Protobuf contracts

`ai/sre/toolgateway/v1/tool_gateway.proto` 定义 Python 调查平面到 Go
工具网关的版本化只读契约。八个工具分别使用独立参数消息和固定 RPC，不存在动态工具注册、任意 URL 或命令执行入口。

契约必须遵守以下约束：

- 每类工具使用独立参数消息，不接受任意 Shell、SQL 或动态代码。
- 请求传播 investigation ID、trace ID、调用者身份和 deadline。
- 变更操作必须在未来通过独立服务绑定审批、目标、参数哈希和幂等键。
- 删除字段时保留字段号和名称。
