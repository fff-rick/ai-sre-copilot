# Evaluations

`stage3-cases.json` 是阶段 3 的五用例冒烟集，用于验证证据引用、Top-3 候选和模型适配器，不替代阶段 6 的 30+ 用例正式评测。

```bash
make eval-offline

# 使用任意 OpenAI-compatible Chat Completions 服务
AI_SRE_MODEL_BASE_URL=https://provider.example/v1 \
AI_SRE_MODEL_API_KEY=... \
AI_SRE_MODEL_ID=... \
make eval-online
```

在线评测不会记录 API Key；输出会记录 Commit、数据集、实际模型标识及逐用例结果。
