# Investigation Service

Python/FastAPI 服务拥有调查领域状态和模型编排。阶段 0 只提供健康检查、领域端口和可确定测试的 Fake 适配器；不访问运维系统凭据。

```bash
uv sync --all-groups
uv run uvicorn ai_sre_investigation.main:app --reload
uv run pytest
```

