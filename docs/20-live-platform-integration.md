# Live Platform 接入计划

## 目标与边界

AI-SRE Copilot 作为 Live Platform 的旁路调查工作台接入，不进入直播请求主链路。
接入顺序固定为只读调查、告警自动触发、知识与发布关联、隔离环境处置验证；在完成权限审计前不连接生产 kubeconfig，也不允许生产变更。

## 当前状态

| 阶段 | 状态 | 交付内容 |
| --- | --- | --- |
| 1. 只读遥测 | 已完成 | Live Platform PromQL、Prometheus、Tempo、Git、独立 Compose 启动配置 |
| 2. 日志证据 | 已完成 | Loki、Alloy 日志采集、统一 `service_name` 标签和 trace ID 关联 |
| 3. 告警触发 | 已完成 | Alertmanager webhook、Bearer 认证、告警字段转换和持久幂等 |
| 4. 知识与发布 | 已完成 | 5 个服务目录、4 个 Runbook、幂等部署事件记录与时间窗关联 |
| 5. 受控处置 | 待实施 | 非生产 Kubernetes 只读检查、审批式重启/扩缩容/回滚 |

## 第一阶段运行方式

先启动 Live Platform：

```bash
cd /home/xin/work/live-platform
docker compose up -d --build
```

再启动 Copilot：

```bash
cd /home/xin/work/ai-sre-copilot
make live-platform-up
```

入口：

- Copilot 工作台：<http://localhost:5173>
- Copilot API：<http://localhost:18000>
- Live Platform：<http://localhost:8080>
- Grafana：<http://localhost:3000>
- Loki：<http://localhost:13100>
- Alertmanager：<http://localhost:19093>

创建调查时，`service` 使用真实遥测服务名：`live-api`、`live-commerce`、
`live-interaction`、`live-identity-room` 或 `live-worker`。

停止 Copilot 不会停止 Live Platform：

```bash
make live-platform-down
```

## 导入服务知识与 Runbook

Live Platform 的知识源保存在其自身仓库的 `doc/ai-sre/catalog.json`，Copilot 通过只读挂载读取。启动两套服务后执行：

```bash
cd /home/xin/work/ai-sre-copilot
make live-platform-knowledge-ingest
```

导入按稳定的 `source_id` 替换文档，可安全重复执行。当前目录覆盖 5 个应用服务，以及 HTTP 5xx、Outbox/Kafka、MySQL 连接池和实时链路四类处置手册。调查会按告警的 `service` 和时间窗口检索这些文档。

## 记录发布事件

部署流程在成功切流后，为每个实际变更的服务记录一条事件：

```bash
cd /home/xin/work/live-platform
make record-release ARGS='--service live-api --version v1.2.3 --revision <git-sha> --environment production --release-id <ci-run-id> --source github-actions'
```

事件追加到被 Git 忽略的 `reports/releases.jsonl`，不会把运行历史提交进源码。相同 `release-id + service` 的流水线重试不会重复写入；同一次多服务发布应复用 `release-id` 并分别记录每个服务。Copilot 的调查流程会通过 `releases.list` 按告警服务和时间窗口采集这些 `type=deployment` 事件，事件中的 `revision` 可作为 Git 变更核验依据。

## 当前已知限制

- 首次成功发布前没有 `reports/releases.jsonl`；部署流程必须调用 `record-release`，否则发布查询会明确记录为 evidence gap。
- 当前 PromQL 先覆盖 HTTP 5xx；Outbox、Kafka 和数据库专项查询在后续证据增强阶段补充。
- Compose 集成不挂载 kubeconfig，并使用不存在的 `live-platform-read-only` namespace，处置操作保持 fail-closed。
- 尚未执行真实模型调查，避免在未确认模型调用成本和数据边界前发送遥测证据。
- 本地 Alertmanager 与 Copilot 使用固定开发令牌；部署到共享环境前必须改为外部 Secret，并在入口启用 TLS。
- Alloy 的 Docker socket 挂载仅用于本地 Compose 日志采集；Kubernetes 环境改用 Pod 日志采集和最小 RBAC。
