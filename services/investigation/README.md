# Investigation Service

Python/FastAPI 服务拥有调查领域状态和模型编排。阶段 3 使用单一显式 LangGraph 工作流执行范围判定、并行证据收集、结构化假设、引用校验、建议和报告；模型不能选择任意工具或控制状态边。

```bash
uv sync --all-groups
uv run uvicorn ai_sre_investigation.main:app --reload
uv run pytest
```

运行时要求 PostgreSQL、Go 工具网关和 OpenAI-compatible 模型配置全部可用。`investigations` 表负责发现、取消与短租约认领，LangGraph PostgreSQL Checkpointer 负责节点级恢复；模型或单一证据源失败会输出有限报告。API：

- `POST /api/v1/investigations`
- `GET /api/v1/investigations/{id}`
- `POST /api/v1/investigations/{id}/cancel`

真实模型凭据只通过 `AI_SRE_MODEL_BASE_URL`、`AI_SRE_MODEL_API_KEY` 和 `AI_SRE_MODEL_ID` 注入。缺少任一必需配置时创建调查返回 503。
