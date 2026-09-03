# 阶段 5 验收记录

## 范围

阶段 5 建立从变更建议到人工审批、Go 网关执行、SLI 复查和审计的隔离闭环。调查 LangGraph 不承担授权；PostgreSQL 审批记录是唯一授权事实源。

## 已实现能力

- `PENDING / APPROVED / REJECTED / EXPIRED / CONSUMED` 持久审批状态机。
- 批准、修改、拒绝、过期和执行 HTTP API；建议人与审批人分离。
- SHA-256 参数绑定、只存摘要的不透明审批令牌、事务性一次消费和幂等执行记录。
- Go protobuf 固定的 Deployment 重启、扩缩容和 Pod template 回滚。
- Python 与 Go 双重隔离 namespace 校验，无 Shell、SQL 或动态变更接口。
- 动作前后 Prometheus SLI 快照、内容哈希与三态恢复判定。
- Web 审批、参数更新、拒绝、执行和恢复结果界面。

## 确定性门禁

```bash
make acceptance-stage5
```

门禁包含：

- Python 单元、类型和覆盖率门禁；
- Go race、权限、参数绑定、幂等和 client-go 变更测试；
- TypeScript lint、类型、交互和覆盖率门禁；
- protobuf 生成漂移、三服务构建和 Compose 校验；
- 临时 kind 集群 + PostgreSQL 的真实跨进程验收。

kind 验收会验证过期令牌拒绝、参数篡改拒绝、重复请求只产生一次副作用，以及 scale/restart/rollback 三类操作和前后审计事件。临时集群在脚本结束后删除。

## 已知限制

- 仅允许启动配置指定的单一测试 namespace，禁止生产环境。
- 回滚仅恢复 Deployment Pod template；Kubernetes revision 不包含副本数。
- 数据库记录写入与 Kubernetes API 副作用不能形成跨系统原子事务。崩溃后保留 `EXECUTING`，调用方必须先查询状态，不能盲目重放。
- 本地 Web 使用演示身份；生产 SSO 与可信身份代理不在阶段 5 范围。

架构权衡见 [ADR 0007](adr/0007-stage5-durable-approval-and-isolated-mutations.md)。
