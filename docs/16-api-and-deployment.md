# V1 API 与部署手册

## 部署判断

V1 推荐 Docker Compose 单机部署用于演示和验收。当前规模不需要 Kubernetes 化产品服务、
Kafka、Redis 或独立向量数据库；Kubernetes 仅用于隔离处置门禁。该部署不是生产 HA 方案。

## 配置与启动

```bash
cp .env.example .env
# 填写 AI_SRE_MODEL_BASE_URL / API_KEY / MODEL_ID
make bootstrap
make eval-offline
make compose-up
```

核心服务被限制为 0.5～1 CPU、128～768 MiB 内存和 128～256 PID，并启用
`no-new-privileges`。`AI_SRE_INVESTIGATION_WORKERS` 默认 5，允许范围 1～16。

运行状态：

```bash
docker compose ps
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8081/health/ready
```

`/health/ready` 只表示 HTTP 进程已启动；缺少模型、数据库或网关配置时，创建调查仍以 503
Fail Closed。功能就绪应以创建调查和 `make acceptance-stage7` 为准。

## HTTP API

开发环境 OpenAPI 位于 <http://localhost:8000/docs>，生产模式关闭 Swagger。主要接口：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/v1/investigations` | 创建有界调查 |
| GET | `/api/v1/investigations` | 分页查询调查 |
| GET | `/api/v1/investigations/{id}` | 获取持久快照与报告 |
| GET | `/api/v1/investigations/{id}/timeline` | 获取持久事件时间线 |
| GET | `/api/v1/investigations/{id}/events` | SSE 增量事件，支持 `Last-Event-ID` |
| GET | `/api/v1/investigations/{id}/evidence/{evidence_id}` | 获取引用证据 |
| POST | `/api/v1/investigations/{id}/cancel` | 请求取消 |
| POST | `/api/v1/investigations/{id}/approvals` | investigator 提交固定类型变更 |
| PUT | `/api/v1/investigations/{id}/approvals/{approval_id}` | 修改参数并使旧授权失效 |
| POST | `.../{approval_id}/approve` | approver 签发短期绑定令牌 |
| POST | `.../{approval_id}/reject` | approver 拒绝申请 |
| POST | `.../{approval_id}/execute` | 使用令牌和幂等键执行并验证 |
| GET | `/api/v1/evaluations/latest` | 返回脱敏后的最新质量报告投影 |

审批接口在 V1 演示中使用 `X-Actor-ID` 和 `X-Actor-Role`。它们是测试身份，不是公网认证机制。
Web 不直接访问 Go 网关；Python 到 Go 使用共享服务凭据、强类型 gRPC 和固定工具集合。

## 评测报告页面

`make eval-offline` 生成 `artifacts/stage6-report.json`。Compose 将 artifacts 目录只读挂载到
Investigation Service，Web 的“质量报告”页通过白名单响应模型读取，不暴露 Prompt、绝对路径、
检查点或工具录制原文。报告缺失时页面提供生成命令，不伪造空指标。

## 备份、停止与升级

```bash
make compose-down                 # 保留数据库与 Artifact 卷
docker compose down --volumes     # 删除数据，不可恢复，需显式执行
```

升级前备份 PostgreSQL 和工具 Artifact 卷。切换 Embedding 模型必须完整重建知识向量；当前表只按
维度过滤，禁止在同一维度下混合两个模型的向量空间。
