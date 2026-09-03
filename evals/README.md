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

`stage4-retrieval-cases.json` 是独立于调查 LLM 的检索基线，验证 catalog 导入、元数据过滤、
关键词/向量 RRF 合并以及 Recall@K/MRR 报告：

```bash
make eval-retrieval
```

它使用确定性 feature-hash embedding，仅验证可复现的检索流水线，不代表线上 embedding
模型质量。报告按语言拆分，防止总体指标掩盖中文词法检索失败。
