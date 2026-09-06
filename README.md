# AI-SRE Copilot

> 一个以证据为中心、默认只读且由人工审批约束的 AI 故障调查与安全处置工作台。

AI-SRE Copilot 面向希望把告警处理从“依赖个人经验”变成“可追溯工程流程”的研发与 SRE 团队。它接收告警，关联指标、日志、链路、Kubernetes 事件、发布记录和历史事故，产出带证据引用的根因假设、下一步验证建议与受控处置方案。

本项目是一个可本地运行的参考实现与工程实验环境：包含 React 工作台、Python 调查服务、Go 可信工具网关、PostgreSQL/pgvector，以及可注入故障的可观测测试床。V1 已完成阶段 0–7 的功能与确定性质量门禁。

## 为什么使用它？

- **减少排障盲区**：将跨指标、日志、链路、发布和知识库的调查过程集中为一条可回放的时间线。
- **让 AI 的结论可审计**：关键假设必须指向原始证据；模型输出不是事实，也不会直接获得执行权限。
- **把安全控制内建到流程中**：工具默认只读；重启、扩缩容和回滚等变更需要审批、RBAC、幂等保护和完整审计。
- **便于验证与二次开发**：附带八类故障场景、冻结评测集、API 契约和本地端到端验证命令。

## 核心能力

- 有界并发、可持久恢复的调查工作流（FastAPI + LangGraph）。
- 基于 PostgreSQL/pgvector 的运行手册与历史事故混合检索。
- 证据工作台：调查时间线、SSE 实时更新、根因 Top-3、证据详情和脱敏质量报告。
- 可信工具网关：Prometheus、Loki、Tempo、Kubernetes 与 Git 连接器，gRPC 契约与审计边界。
- 人工在环的隔离处置闭环：审批令牌、命名空间限制、幂等与 fail-closed 设计。
- 可观测测试床：可注入错误率、延迟、CPU、内存、依赖、配置、连接池与发布回归等故障。

## 架构

```text
React / TypeScript Web
          |
          v
Python Investigation Service ── FastAPI · LangGraph · RAG · Eval
          |
          | gRPC
          v
Go Tool Gateway ── RBAC · Audit · Prometheus · Loki · Tempo · Kubernetes · Git
          |
          v
Observable Testbed
```

## 快速开始

### 1. 准备环境

完整本地体验需要 Git、Docker Engine（含 Compose v2）、`curl`、`jq`，以及一个支持严格 JSON Schema 输出的 OpenAI-compatible Chat Completions 模型端点。运行源码测试、离线评测或知识导入还需要 `uv`、Go、Node.js 和 pnpm。

```bash
git clone https://github.com/fff-rick/ai-sre-copilot.git
cd ai-sre-copilot
cp .env.example .env
```

编辑 `.env`，填入模型配置（不要提交该文件）：

```dotenv
AI_SRE_MODEL_BASE_URL=https://provider.example/v1
AI_SRE_MODEL_API_KEY=replace-with-your-secret
AI_SRE_MODEL_ID=replace-with-model-id
```

### 2. 启动可演练环境

下面的命令会生成离线质量报告、启动故障测试床并启动 Copilot：

```bash
make eval-offline
make testbed-up
make testbed-smoke
make compose-up
```

打开以下入口：

| 服务 | 地址 |
| --- | --- |
| AI-SRE 工作台 | <http://localhost:5173> |
| Investigation API / Swagger | <http://localhost:8000/docs> |
| Tool Gateway health | <http://localhost:8081/health/ready> |
| Grafana | <http://localhost:13000> |
| Prometheus | <http://localhost:19090> |

### 3. 注入故障并创建调查

在另一个终端注入支付服务错误率故障并制造请求：

```bash
./testbed/scripts/fault.sh inject errors-payment

for _ in 1 2 3 4 5; do
  curl -sS -o /dev/null -X POST http://localhost:18080/checkout \
    -H 'Content-Type: application/json' \
    -d '{"sku":"widget-red","quantity":1,"amount_cents":1299}' || true
done
```

然后访问工作台，填写服务 `payment`、严重度和摘要，点击“创建调查”。你将看到调查节点实时进入时间线，并在完成后查看根因假设、证据引用和建议操作。也可通过 Swagger 直接调用 API。

体验结束后恢复服务：

```bash
./testbed/scripts/fault.sh recover payment
make testbed-smoke
make compose-down
```

> 未配置模型时，健康检查仍可用于部署诊断，但创建调查会返回 HTTP 503；系统不会降级为不可持久化、不可审计的模型调用。

## 常用命令

```bash
make bootstrap          # 安装锁定的 Python 与 Web 依赖
make lint               # 静态检查
make test               # Python、Go、Web 与测试床单元测试
make acceptance-stage7 # 完整 V1 确定性发布候选门禁
make eval-offline       # 运行 32 个冻结用例的离线评测
make compose-down       # 停止 Copilot，保留本地数据库卷
```

运行全部验收前，请确认 Docker、kind、uv、Go、Node.js 与 pnpm 已就绪。详细前置条件和排障步骤见[本地使用手册](docs/14-local-user-guide.md)。

## 安全边界与适用范围

- 这是面向本地开发、评估和受控集成的参考实现，**不要直接连接生产 Kubernetes 集群或生产凭据**。
- 默认 Compose 没有 kubeconfig；涉及重启、扩缩容和回滚的工具会拒绝执行。这是预期的 fail-closed 行为。
- 变更能力仅应在隔离的 kind 集群和 `MUTATION_ALLOWED_NAMESPACE` 指定的测试命名空间中验证。
- 请始终把 `.env`、API Key、kubeconfig、生产日志和用户数据留在版本控制之外；提交前应执行密钥扫描。

威胁模型、部署前置条件和安全设计详见[威胁模型](docs/15-threat-model.md)与[V1 API 与部署手册](docs/16-api-and-deployment.md)。

## 项目结构

```text
web/                       React + TypeScript 工作台
services/investigation/    FastAPI 调查工作流、RAG 与评测
services/tool-gateway/     Go 可信工具网关
testbed/                   可观测故障测试环境
knowledge/                 示例服务知识、Runbook 与历史事故
evals/                     冻结评测集与评测脚本
proto/                     版本化 gRPC 契约
docs/                      架构、ADR、验证记录和使用手册
```

## 文档

- [本地使用手册](docs/14-local-user-guide.md)：从启动到完成一次调查的详细操作。
- [架构设计](docs/04-architecture-design.md)：服务边界与关键数据流。
- [验收测试](docs/06-acceptance-tests.md)：各阶段验收范围。
- [工程基线](docs/07-engineering-baseline.md)：工具链、质量标准和约束。
- [演示手册](demo/README.md)：固定演示与录制步骤。
- [ADR](docs/adr/)：关键架构决策及其取舍。

## 贡献

欢迎通过 Issue 或 Pull Request 参与。提交前请运行与改动相符的检查，至少执行：

```bash
make lint
make test
```

请勿提交任何密钥、真实生产数据、kubeconfig 或包含敏感信息的 Artifact。涉及工具权限、审批或数据访问的改动，请同时更新相应测试和威胁模型。

## 许可证

本项目采用 [MIT License](LICENSE)。
