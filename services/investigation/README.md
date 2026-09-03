# Investigation Service

Python/FastAPI 服务拥有调查领域状态、模型编排和独立的人工审批状态机。LangGraph 负责只读调查与恢复，PostgreSQL 审批记录负责授权生命周期；模型不能选择任意工具、控制状态边或签发变更权限。

```bash
uv sync --all-groups
uv run uvicorn ai_sre_investigation.main:app --reload
uv run pytest
```

运行时要求 PostgreSQL、Go 工具网关和 OpenAI-compatible 模型配置全部可用。`investigations` 表负责发现、取消与短租约认领，LangGraph PostgreSQL Checkpointer 负责节点级恢复；模型或单一证据源失败会输出有限报告。API：

- `POST /api/v1/investigations`
- `GET /api/v1/investigations/{id}`
- `GET /api/v1/investigations`
- `GET /api/v1/investigations/{id}/timeline`
- `GET /api/v1/investigations/{id}/events`（SSE，支持 `Last-Event-ID`）
- `GET /api/v1/investigations/{id}/evidence/{evidence_id}`
- `POST /api/v1/investigations/{id}/cancel`
- `POST/GET /api/v1/investigations/{id}/approvals`
- `PUT /api/v1/investigations/{id}/approvals/{approval_id}`
- `POST /api/v1/investigations/{id}/approvals/{approval_id}/approve|reject|execute`
- `GET /api/v1/investigations/{id}/remediation-audit`

真实模型凭据只通过 `AI_SRE_MODEL_BASE_URL`、`AI_SRE_MODEL_API_KEY` 和 `AI_SRE_MODEL_ID` 注入。嵌入服务通过 `AI_SRE_EMBEDDING_*` 配置；base URL 和 API Key 未单独设置时继承模型配置。缺少调查运行时任一必需配置时创建调查返回 503。

知识目录使用受版本控制的 JSON catalog 和 Markdown 文档：

```bash
AI_SRE_DATABASE_URL=... \
AI_SRE_EMBEDDING_BASE_URL=... \
AI_SRE_EMBEDDING_API_KEY=... \
AI_SRE_EMBEDDING_MODEL_ID=... \
uv run ai-sre-ingest ../../knowledge/catalog.json
```
