# Protobuf contracts

阶段 0 只冻结服务职责和错误分类，不提前猜测工具参数。阶段 2 将在此目录定义版本化的 Tool Gateway gRPC 契约、Buf lint/生成配置和兼容性门禁。

契约必须遵守以下约束：

- 每类工具使用独立参数消息，不接受任意 Shell、SQL 或动态代码。
- 请求传播 investigation ID、trace ID、调用者身份和 deadline。
- 变更操作绑定审批、目标、参数哈希和幂等键。
- 删除字段时保留字段号和名称。

