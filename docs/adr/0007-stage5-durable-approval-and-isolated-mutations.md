# ADR 0007：持久审批授权与隔离变更边界

- 状态：Accepted
- 日期：2026-09-03
- 决策范围：阶段 5 人在回路、Go 变更网关、Kubernetes 隔离处置

## 背景

阶段 3/4 的 LangGraph 检查点用于恢复调查计算，不是授权账本。阶段 5 的审批可能跨越分钟或小时，并且必须支持撤销、过期、参数变更失效、一次消费、跨进程幂等和独立审计。任何模型输出、日志或 Runbook 内容都不能直接成为变更权限。

Kubernetes 的 Deployment 回滚也有明确语义限制：扩缩容不会创建 Deployment revision，回滚恢复的是 Pod template，不会自动恢复副本数等全部字段。因此网关不能把“回滚”描述成通用配置恢复。

## 比较方案

### 方案 A：使用 LangGraph interrupt/checkpoint 保存审批

优点是节点衔接直接。缺点是把模型工作流状态和安全授权状态耦合，Go 网关仍无法独立验证，撤销、一次消费和跨进程并发还需要另一套事务记录。拒绝采用。

### 方案 B：自包含 JWT 审批令牌

JWT 可以离线验证签名与过期时间，但参数修改后的即时撤销、一次消费和幂等执行仍然需要数据库；同时引入签名密钥轮换、`kid`、算法约束和撤销表。它没有消除持久状态，反而增加两套授权语义。V1 拒绝采用。

### 方案 C：PostgreSQL 中的审批记录 + 高熵不透明令牌

批准时生成高熵随机令牌，只保存 SHA-256 摘要。Go 网关在一个行锁事务内重新校验调查、工具、目标、规范化参数哈希、有效期、状态和幂等键，然后把审批标记为已消费并先写入 `EXECUTING` 执行记录。采用此方案。

## 决策

1. 调查工作流只生成建议；审批与执行使用独立的持久状态机：`PENDING -> APPROVED -> CONSUMED`，以及 `REJECTED`、`EXPIRED` 分支。
2. 修改任何目标或参数会清空原令牌、审批人和有效期，并回到 `PENDING`。
3. Go 网关只接受 protobuf 定义的 `restart_deployment`、`scale_deployment` 和 `rollback_deployment`；不提供 Shell、SQL、字符串命令或动态工具注册。
4. 变更目标必须精确位于启动配置指定的单一隔离 namespace。Python 与 Go 两侧都校验，但 Go 是最终强制边界。
5. 执行记录在外部副作用前提交。相同幂等键和相同参数返回原记录；相同键绑定不同操作时返回冲突。进程在 `EXECUTING` 后崩溃时不得盲目重放，应先调用 `GetMutationExecution` 并人工协调。这是对“最多一次发起”的保守选择，不宣称分布式严格 exactly-once。
6. Kubernetes 乐观锁冲突使用 client-go 有界重试。回滚仅复制目标 ReplicaSet 的 Pod template；扩缩容使用 scale subresource。
7. 变更前后各保存一次带查询、时间、来源和内容哈希的 SLI 快照，并显式标记 `RECOVERED`、`NOT_RECOVERED` 或 `UNABLE_TO_DETERMINE`。

## 权衡

- 复杂度：新增三张关系表和跨语言共享模式，但移除了 JWT 密钥生命周期及第二套撤销机制。
- 可靠性：数据库不可用时 fail closed，不会产生无审计副作用；数据库提交与 Kubernetes API 之间仍存在不可原子化窗口，因此保留 `EXECUTING` 供协调。
- 性能：每次变更多一次短事务和行锁；变更是低频人工操作，此成本可接受。
- 可维护性：固定类型工具和单 namespace 策略容易测试。增加新动作必须同时修改 protobuf、风险策略、连接器和验收，不允许运行时注册。
- 扩展性：单 PostgreSQL 足以支撑 V1。只有审批吞吐或跨区域需求出现后，才评估独立工作流引擎或消息系统。

## 安全边界与限制

本阶段只面向隔离测试环境。HTTP 层的 `X-Actor-*` 头被视为由可信身份代理注入；本地演示没有实现企业 SSO，不能部署为生产审批入口。无论 HTTP 身份来源如何，Go 网关仍要求服务凭据、`approver` 角色和一次性审批令牌。生产身份接入与更细 RBAC 留给工程加固阶段。

## 参考

- [Kubernetes Deployments：回滚与 revision 语义](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
- [Kubernetes API：Scale 资源](https://kubernetes.io/docs/reference/kubernetes-api/workload-resources/scale-v1/)
- [PostgreSQL 显式锁与 `SELECT FOR UPDATE`](https://www.postgresql.org/docs/current/explicit-locking.html)
