# 阶段 1 验收记录

验收日期：2026-09-02。

## 验收范围

本轮验收覆盖 API → Order → Inventory → PostgreSQL 与 Order → Payment 业务链路、OpenTelemetry Collector、Prometheus、Loki、Tempo、Grafana、cAdvisor，以及八类阶段 1 故障。

## 结果

阶段 1 的三个退出条件全部满足：

1. 同一请求可通过 `trace_id` 在指标、日志和 Trace 中关联。
2. 八个核心故障可通过命令重复注入，并可显式恢复或自动到期。
3. 场景 ID、目标、开始时间、到期时间和恢复事件自动写入 JSONL Artifact 与结构化日志。

| ID | 故障 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| GT-S1-001 | 库存延迟 | 通过 | 2500 ms 注入后结账耗时超过 2.4 s，Trace 慢点位于库存 |
| GT-S1-002 | 支付错误率 | 通过 | Payment 503、API 502，恢复后结账成功 |
| GT-S1-003 | 订单 CPU 饱和 | 通过 | 应用报告 2 个 worker，cAdvisor CPU 速率超过 0.20 core |
| GT-S1-004 | 支付内存压力 | 通过 | 应用报告 67108864 bytes，cAdvisor 工作集增长超过 50 MiB |
| GT-S1-005 | 库存连接池耗尽 | 通过 | pgxpool acquired=2、max=2，等待 2500 ms 后失败 |
| GT-S1-006 | 支付依赖不可用 | 通过 | Order 真实客户端连接被拒绝，API 502，Payment 未处理 charge |
| GT-S1-007 | 支付路径配置错误 | 通过 | `/charge` 被替换为 `/charge-v2`，Payment 返回 404 |
| GT-S1-008 | 支付发布回归 | 通过 | 1.1.0 仅使 `widget-red` 失败，`widget-blue` 保持成功 |

GT-S1-008 的一次错误请求在 Tempo 中形成跨 API、Order、Inventory、Payment 的 8-span Trace。Loki 中六个新增场景均能按场景 ID 查询，其中连接池日志记录 `pool_acquired=2`、`pool_max=2`，发布日志同时记录 `previous_version=1.0.0` 和 `release_version=1.1.0`。

## 自动复现

```bash
make compose-config
make test-testbed
make testbed-up
make testbed-validate
```

`testbed-validate` 依次执行八个场景，任一状态码、耗时或资源指标断言失败都会返回非零；退出时使用 trap 清理四个服务的活动故障，最后执行正常结账 Smoke Test。

运行期事件写入 `testbed/artifacts/fault-events/events.jsonl`。该本地产物不进入版本控制。

## 适用边界

- 内存场景验证有界压力和证据链，不主动制造 OOM kill。
- 发布场景验证版本时间相关性和输入范围，不等价于真实镜像滚动发布。
- 性能数值只适用于当前本地 Compose 环境，不代表生产容量。
