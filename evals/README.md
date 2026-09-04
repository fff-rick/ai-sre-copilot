# Evaluations

`stage6-cases.json` 是内容哈希冻结的正式离线评测集。8 个故障模板与 4 个服务/时间/噪声
变体展开为 32 个用例，同一批工具记录比较 `hypotheses-v2` 与 `evidence-first-v3`：

```bash
make eval-offline

# 输出 artifacts/stage6-report.json、stage6-report.md 和逐用例检查点
```

回放模式使用脚本化模型替身验证评分、失败分类、安全和 CI 门禁，不能替代真实模型质量
评测。在线模式复用相同数据集、工具记录和评分器，并要求显式提供当前模型价格：

```bash
AI_SRE_MODEL_BASE_URL=https://provider.example/v1 \
AI_SRE_MODEL_API_KEY=... \
AI_SRE_MODEL_ID=... \
AI_SRE_MODEL_INPUT_USD_PER_MILLION=... \
AI_SRE_MODEL_OUTPUT_USD_PER_MILLION=... \
make eval-online
```

在线评测不会记录 API Key；输出记录 Commit、数据集、实际模型标识、Prompt 哈希、逐用例
Token、估算成本、Trace ID、检查点和工具记录哈希。强制安全项或 V1 门槛失败时返回非零。

`stage3-cases.json` 保留为五用例工作流冒烟集：

```bash
make eval-stage3-smoke
make eval-stage3-online
```

`stage4-retrieval-cases.json` 是独立于调查 LLM 的检索基线，验证 catalog 导入、元数据过滤、
关键词/向量 RRF 合并以及 Recall@K/MRR 报告：

```bash
make eval-retrieval
```

它使用确定性 feature-hash embedding，仅验证可复现的检索流水线，不代表线上 embedding
模型质量。报告按语言拆分，防止总体指标掩盖中文词法检索失败。
