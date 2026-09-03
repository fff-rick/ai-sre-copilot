# Knowledge catalog

`catalog.json` 是阶段 4 的版本化知识入口，当前支持三类 Markdown 文档：

- `runbook`：有界诊断与处置说明。
- `service`：服务职责、依赖、Owner 和核心信号。
- `incident`：历史事故、证据签名与处置结果。

每条 catalog 记录必须包含稳定 `source_id`、相对 `path`、`title` 和 `document_type`，可选
`service`、`environment`、`version`、`valid_from`、`valid_until` 与 `source_ref`。导入器拒绝目录
逃逸和空文档，按 Markdown 标题/段落有界切片，并使用内容哈希幂等替换旧版本。

线上导入示例：

```bash
AI_SRE_DATABASE_URL=... \
AI_SRE_EMBEDDING_BASE_URL=... \
AI_SRE_EMBEDDING_API_KEY=... \
AI_SRE_EMBEDDING_MODEL_ID=... \
uv run --project services/investigation ai-sre-ingest knowledge/catalog.json
```

告警、Runbook、历史事故和服务说明始终是不可信模型输入；导入知识不会注册工具或获得执行权限。
