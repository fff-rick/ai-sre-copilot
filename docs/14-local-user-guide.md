# 本地使用手册

## 1. 当前可体验范围

Web 前端已经接入真实后端，不是静态 Mock。浏览器请求由 Nginx 将 `/api/` 转发给 FastAPI，
当前支持调查列表、持久化时间线、SSE 实时更新、根因假设、证据详情和审批操作。

但当前版本仍有以下边界：

| 能力 | 当前状态 | 本地体验方式 |
|---|---|---|
| 可观测测试业务链路 | 已实现 | `make testbed-up` |
| 调查列表、时间线、根因与证据 | Web 已接通 | 浏览器访问 `http://localhost:5173` |
| 创建调查 | 后端已实现，Web 尚无表单 | Swagger 或 `curl` |
| 调查模型调用 | 已实现 | 必须配置支持严格 JSON Schema 的 OpenAI-compatible 模型 |
| Runbook/历史事故检索 | 已实现，可选 | 配置 Embedding 并导入知识目录 |
| 提交、修改、批准和拒绝 | Web 已接通 | 默认 Compose 可体验 |
| 重启、扩缩容和回滚 | 仅允许隔离 kind | 使用 `make test-stage5-kind` 自动验收，不建议连接生产集群 |
| 阶段 6 评测报告 | 已实现为 Artifact | `make eval-offline`；Web 尚无报告页面 |

默认 Compose 没有 Kubernetes kubeconfig，因此 Web 中“执行并验证”会被可信工具网关拒绝。
这是 fail-closed 安全行为，不是前端断线。审批令牌只保存在当前浏览器内存中，批准后刷新页面
会丢失该令牌。

## 2. 环境要求

最短的容器化体验只需要：

- Git。
- Docker Engine 或 Docker Desktop。
- Docker Compose v2，即 `docker compose` 命令。
- `curl` 和 `jq`。
- 一个 OpenAI-compatible Chat Completions 模型端点。该端点必须支持
  `response_format.type=json_schema` 的严格结构化输出。

执行源码测试、知识导入和离线评测还需要 `uv`、Go、Node.js 和 pnpm；执行隔离变更验收还
需要 `kind`。版本以 CI 和工程基线为准。

检查基础环境：

```bash
docker version
docker compose version
curl --version
jq --version
```

## 3. 配置模型

进入仓库并创建本地配置：

```bash
cd /home/xin/work/ai-sre-copilot
cp .env.example .env
```

编辑 `.env`，至少填写：

```dotenv
AI_SRE_MODEL_BASE_URL=https://provider.example/v1
AI_SRE_MODEL_API_KEY=replace-with-local-secret
AI_SRE_MODEL_ID=replace-with-model-id
```

不要提交 `.env`。如果三个模型变量任一缺失，容器健康检查仍可能显示 healthy，但调查运行时
不会创建，调查列表和创建接口会返回 HTTP 503。因此不能只用 `/health/ready` 判断模型运行时
是否可用。

## 4. 启动完整只读体验环境

先启动测试业务和可观测组件，再启动 Copilot：

```bash
make testbed-up
make testbed-smoke
make compose-up
```

检查容器和接口：

```bash
docker compose -f testbed/compose.yaml ps
docker compose ps

curl -fsS http://localhost:8000/health/ready | jq
curl -fsS http://localhost:8081/health/ready | jq
curl -fsS http://localhost:8000/api/v1/investigations | jq
```

入口地址：

| 页面/服务 | 地址 |
|---|---|
| AI-SRE Web 工作台 | <http://localhost:5173> |
| FastAPI Swagger | <http://localhost:8000/docs> |
| Grafana | <http://localhost:13000> |
| Prometheus | <http://localhost:19090> |
| Loki | <http://localhost:13100> |
| Tempo | <http://localhost:13200> |
| 测试业务 API | <http://localhost:18080> |

## 5. 完成一次故障调查

以下示例注入支付服务 100% 错误率，并在故障生效期间生成观测数据：

```bash
./testbed/scripts/fault.sh inject errors-payment

for _ in 1 2 3 4 5; do
  curl -sS -o /dev/null -X POST http://localhost:18080/checkout \
    -H 'Content-Type: application/json' \
    -d '{"sku":"widget-red","quantity":1,"amount_cents":1299}' || true
done

sleep 8
```

创建覆盖最近 15 分钟的调查。下面的时间命令适用于 GNU/Linux；macOS 可安装 coreutils 后将
`date` 替换为 `gdate`：

```bash
START=$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

CREATE_RESPONSE=$(
  jq -n \
    --arg start "$START" \
    --arg end "$END" \
    '{
      alert: {
        alert_id: "manual-payment-errors",
        service: "payment",
        severity: "critical",
        summary: "Payment error rate is 100 percent after fault injection.",
        time_window: {start: $start, end: $end},
        source_ref: "manual://local-demo/payment-errors",
        labels: {environment: "local-testbed"}
      },
      budget: {
        max_model_calls: 3,
        max_tool_calls: 6,
        max_total_seconds: 180,
        max_input_tokens: 30000,
        max_output_tokens: 8000,
        max_verification_rounds: 2
      },
      model_profile: "local-demo"
    }' |
    curl -fsS http://localhost:8000/api/v1/investigations \
      -H 'Content-Type: application/json' \
      --data-binary @-
)

echo "$CREATE_RESPONSE" | jq
INVESTIGATION_ID=$(echo "$CREATE_RESPONSE" | jq -r '.investigation.investigation_id')
echo "http://localhost:5173/?investigation=$INVESTIGATION_ID"
```

打开输出的 Web 地址。如果页面此前一直处于“暂无调查记录”，创建后刷新一次浏览器。随后可
观察：

1. 左侧调查状态和严重级别。
2. 调查节点通过 SSE 进入时间线。
3. 调查完成后的 Top-3 根因、置信度和验证状态。
4. 点击证据 ID 打开查询、来源、片段和 SHA-256 详情。
5. 提交变更建议，体验批准、修改或拒绝状态机。

也可以直接查询状态和时间线：

```bash
curl -fsS "http://localhost:8000/api/v1/investigations/$INVESTIGATION_ID" | jq
curl -fsS "http://localhost:8000/api/v1/investigations/$INVESTIGATION_ID/timeline" | jq
```

体验结束后恢复支付服务：

```bash
./testbed/scripts/fault.sh recover payment
make testbed-smoke
```

## 6. 体验其他故障

每次注入后都应发送对应业务请求并等待至少一个指标采集周期：

```bash
./testbed/scripts/fault.sh inject latency-inventory
./testbed/scripts/fault.sh inject cpu-order
./testbed/scripts/fault.sh inject memory-payment
./testbed/scripts/fault.sh inject pool-inventory
./testbed/scripts/fault.sh inject dependency-payment
./testbed/scripts/fault.sh inject config-payment-path
./testbed/scripts/fault.sh inject release-payment
```

恢复命令使用目标服务名：

```bash
./testbed/scripts/fault.sh recover inventory
./testbed/scripts/fault.sh recover order
./testbed/scripts/fault.sh recover payment
```

`make testbed-validate` 会自动依次注入、断言并恢复全部八个阶段 1 场景，适合验证测试床，
但不会自动创建 AI 调查。

## 7. 可选：启用知识检索

知识检索要求模型端点或单独端点支持 Embedding。先在 `.env` 配置：

```dotenv
AI_SRE_EMBEDDING_BASE_URL=https://provider.example/v1
AI_SRE_EMBEDDING_API_KEY=replace-with-local-secret
AI_SRE_EMBEDDING_MODEL_ID=replace-with-embedding-model-id
```

安装锁定依赖并导入知识目录：

```bash
make bootstrap

set -a
source .env
set +a

AI_SRE_DATABASE_URL=postgresql://ai_sre:local-development-only@127.0.0.1:5432/ai_sre \
uv run --project services/investigation ai-sre-ingest knowledge/catalog.json

docker compose up -d --force-recreate investigation web
```

`source .env` 会把本地配置导入当前终端，请只在可信本机使用，并避免用 `env`、Shell 调试
模式或命令历史打印 API Key。

## 8. 隔离处置验收

默认 Compose 可以体验审批记录，但没有 Kubernetes 执行权限。验证真正的 restart、scale、
rollback 和安全阻断应使用自动化 kind 门禁：

```bash
kind version
make test-stage5-kind
```

该命令创建临时 `ai-sre-stage5` 集群和 `ai-sre-test` namespace，验证过期审批、参数篡改、
幂等、重启、扩缩容和回滚，结束后自动删除集群。它是跨进程安全验收，不提供交互式 Web
会话。不要将默认 Web 工作台连接到生产 kubeconfig。

## 9. 运行阶段 6 离线评测

```bash
make bootstrap
make eval-offline
```

输出位于：

- `artifacts/stage6-report.json`
- `artifacts/stage6-report.md`
- `artifacts/stage6/checkpoints/`

离线评测展开 32 个冻结用例并比较两个 Prompt。它验证回放、评分和 CI 门禁，不代表真实模型
效果。真实模型评测还需要设置模型价格：

```bash
AI_SRE_MODEL_INPUT_USD_PER_MILLION=... \
AI_SRE_MODEL_OUTPUT_USD_PER_MILLION=... \
make eval-online
```

## 10. 常见问题

### Web 显示“无法读取调查列表”

首先检查模型变量和 Investigation 日志：

```bash
docker compose logs --tail=200 investigation
docker compose exec investigation env | grep '^AI_SRE_MODEL_' | sed 's/=.*/=<configured>/'
```

最常见原因是模型 URL、API Key 或模型 ID 缺失。API Key 不应打印到终端或日志。

### 模型返回结构化输出错误

当前适配器调用 `/chat/completions` 并要求严格 JSON Schema。只支持普通 JSON mode、但不支持
`json_schema` 的兼容服务不能直接运行完整调查，需要更换端点或扩展模型适配器。

### 调查有证据但结论质量一般

当前在线调查查询模板与测试床的 OpenTelemetry 导出名称还没有完全统一：部分 Prometheus
指标和 Loki 标签可能返回空结果，而不是测试床没有产生数据。可先在 Grafana/Prometheus/
Loki 确认原始观测，再查看报告中的 `evidence_gaps`。这是当前产品化缺口，不能通过反复调大
模型预算解决。

### Web 执行变更失败

默认 Compose 未配置 Kubernetes 客户端，这是预期行为。使用 `make test-stage5-kind` 验证隔离
处置；不要为了演示绕过 namespace、令牌或参数哈希校验。

### 查看服务日志

```bash
docker compose logs -f investigation tool-gateway web
docker compose -f testbed/compose.yaml logs -f api order inventory payment
```

## 11. 停止与清理

停止服务但保留数据库和观测数据：

```bash
make compose-down
make testbed-down
```

如需删除全部本地数据卷，可执行以下命令。该操作不可恢复，会删除调查、审批、评测外数据和
测试床观测历史：

```bash
docker compose down -v
docker compose -f testbed/compose.yaml down -v
```

## 12. 推荐体验顺序

1. 先运行 `make testbed-up && make testbed-smoke`，在 Grafana 查看正常调用链。
2. 配置模型并运行 `make compose-up`，通过 curl 创建一条调查，在 Web 查看时间线和证据。
3. 注入 `errors-payment` 或 `latency-inventory`，比较故障前后的观测和调查结果。
4. 在 Web 体验审批状态，但用 `make test-stage5-kind` 验证真正的隔离变更。
5. 最后运行 `make eval-offline` 查看 32 用例质量报告。

这个顺序能展示工程边界，又不会把首次体验变成 Kubernetes 网络和证书配置工作。
