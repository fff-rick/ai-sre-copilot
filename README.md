# AI-SRE Copilot

AI-SRE Copilot 是一个面向中小研发团队的智能故障调查与安全处置系统。它从告警出发，关联指标、日志、链路、Kubernetes 事件、发布记录和历史事故，输出带证据的根因假设、处置建议与复盘报告。

项目已完成阶段 0～3。Python 调查工作流已通过确定性门禁、PostgreSQL 跨实例恢复和真实模型五故障在线验收；下一阶段进入知识检索与证据工作台。V1 聚焦“调查正确、证据可查、安全可控”，不追求无人值守自愈。

## 核心原则

- Evidence First：每条关键结论必须关联原始证据。
- Read-only by Default：默认仅开放只读工具。
- Human in the Loop：重启、扩缩容、回滚等变更必须人工审批。
- Bounded Autonomy：使用显式状态机约束 Agent，而不是无边界自主循环。
- Observable and Evaluable：每次模型、检索、工具调用和状态转换都可追踪、可回放、可评测。
- Simple Before Scale：V1 不引入 Kafka、Temporal、Elasticsearch 或多 Agent 集群。

## 目标架构

```text
React / TypeScript Web
          |
          v
Python Investigation Service
FastAPI + LangGraph + RAG + Eval
          |
          | gRPC
          v
Go Tool Gateway
RBAC + Audit + Prometheus/Loki/Tempo/Kubernetes/Git Connectors
          |
          v
Observable Testbed
```

## 工程文档

1. [项目立项背景](docs/01-project-background.md)
2. [项目目标](docs/02-project-goals.md)
3. [技术栈与选型](docs/03-technology-stack.md)
4. [架构设计](docs/04-architecture-design.md)
5. [阶段性任务](docs/05-roadmap.md)
6. [验收测试](docs/06-acceptance-tests.md)
7. [工程基线](docs/07-engineering-baseline.md)
8. [阶段 1 验收记录](docs/08-stage1-validation.md)
9. [阶段 2 验收记录](docs/09-stage2-validation.md)
10. [阶段 3 验收记录](docs/10-stage3-validation.md)

## 当前可运行基线

仓库已包含三个服务和 PostgreSQL + pgvector 本地环境：

```text
web/                       React + TypeScript 静态应用
services/investigation/    FastAPI + LangGraph 调查工作流与持久恢复
services/tool-gateway/     Go 可信工具网关与只读工具注册表
proto/                     阶段 2 的版本化契约边界
testbed/                   阶段 1 的可观测故障环境
evals/                     阶段 6 的冻结评测集
deploy/                    本地基础设施初始化
```

本地已安装 Python 3.14、Go 1.26、Node.js 24 和 pnpm 10 时：

```bash
cp .env.example .env
make bootstrap
make lint
make test
make compose-up
```

启动后可访问：

- Web：<http://localhost:5173>
- Investigation 健康检查：<http://localhost:8000/health/ready>
- Tool Gateway 健康检查：<http://localhost:8081/health/ready>
- Tool Gateway gRPC：`localhost:9091`

`make compose-down` 会停止服务但保留本地数据库卷和脱敏 Artifact。模型配置缺失时，健康检查仍可用于部署诊断，但创建调查返回 503，不会执行不可持久化或不可审计的降级流程。

阶段 2 的完整本地门禁为：

```bash
make acceptance-stage2
```

其中常规 PR 使用 Fake client-go 验证 Kubernetes 契约；阶段验收另使用临时 kind 集群验证真实 API Server，结束后删除集群。

阶段 3 的确定性门禁及真实模型冒烟评测为：

```bash
make acceptance-stage3

AI_SRE_MODEL_BASE_URL=https://provider.example/v1 \
AI_SRE_MODEL_API_KEY=... \
AI_SRE_MODEL_ID=... \
make eval-online
```

阶段 1 的可观测测试床使用独立 Compose 项目，避免拖慢日常工程基线：

```bash
make testbed-up
make testbed-smoke
```

架构、故障注入和观测入口见 [Testbed 文档](testbed/README.md)。

## V1 交付定义

V1 完成时，系统应能在可重复的测试环境中：

1. 接收告警并创建一次可持久化的调查任务。
2. 调用至少 8 个只读工具收集多源证据。
3. 输出根因 Top-3、置信度、证据引用和建议操作。
4. 在中断或服务重启后恢复调查。
5. 对危险操作执行审批、权限校验和完整审计。
6. 使用不少于 30 个故障用例执行自动回归评测。

## 项目边界

V1 不连接真实生产环境，不承诺自动修复全部故障，不以聊天机器人作为主要交互形式，也不将 LLM 输出直接视为事实或执行授权。
