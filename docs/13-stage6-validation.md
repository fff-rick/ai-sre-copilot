# 阶段 6 验收记录

## 范围

阶段 6 将阶段 0～5 的调查与安全边界固定为可重复的评测系统：32 个故障用例、脱敏工具
记录、内容冻结、离线回放、双 Prompt 对比、质量/安全门禁和可诊断报告。

## 已实现能力

- `stage6-faults-v1` 覆盖延迟、错误率、数据库、资源、网络、配置、发布和容量 8 个故障族。
- 每个故障族包含服务名、环境、时间窗口和噪声差异，合计 32 个唯一用例；8 个变体包含
  Prompt Injection 强制安全检查。
- `RecordingToolClient` 在录制边界递归脱敏敏感键、Bearer/AWS/API Key 形态，并使用
  SHA-256 冻结完整记录。
- `ReplayToolClient` 校验记录哈希并精确匹配工具名与参数，未录制请求使用稳定错误拒绝。
- 同一冻结工具证据可比较 `hypotheses-v2` 和 `evidence-first-v3`，输出总体及按故障族指标。
- JSON 与 Markdown 报告包含完成率、Top-1/Top-3、引用、无依据陈述、工具成功率、P50/P95
  延迟、Token、估算成本、安全和 Trace 完整率。
- 六类失败统一为检索、工具、推理、引用、权限和预算失败；每个用例保留 Trace ID、检查点
  Artifact 和工具记录 URI/哈希。
- GitHub Actions 在 PR 执行离线门禁并上传报告与检查点；安全强制项失败时命令返回非零。

## 确定性门禁

```bash
make eval-offline
make acceptance-stage6
```

阶段 6 门禁运行全语言单测、静态检查、契约漂移、构建、Compose 校验和离线评测。阶段 5 的
kind 安全门禁继续作为 PR 中独立并行任务执行，避免重复串行拉取集群镜像，同时不降低合并
要求。

阶段 6 回放基线展开 32 个用例。`evidence-first-v3` 的确定性回放结果达到：完成率 100%、
Top-1 100%、Top-3 100%、有效引用率 100%、无依据陈述率 0%、只读工具成功率 100%、
安全通过率 100% 和 Trace 完整率 100%。报告生成于忽略提交的 `artifacts/`，不把机器相关
延迟写死在文档中。

这些数字验证评测与门禁实现，不代表真实模型准确率。真实模型评测需要明确模型和价格：

```bash
AI_SRE_MODEL_BASE_URL=https://provider.example/v1 \
AI_SRE_MODEL_API_KEY=... \
AI_SRE_MODEL_ID=... \
AI_SRE_MODEL_INPUT_USD_PER_MILLION=... \
AI_SRE_MODEL_OUTPUT_USD_PER_MILLION=... \
make eval-online
```

阶段 7 的发布候选报告必须包含一次真实模型运行；没有真实模型报告时不得将离线回放数字
用于对外宣称模型效果。

## 架构权衡

当前规模不引入 LangSmith、DeepEval、独立评测数据库或任务队列。其托管实验、Judge 和协作
能力有价值，但不是可复现安全门禁的必要组件。选择和演进条件见
[ADR 0008](adr/0008-frozen-replay-quality-gates.md)。
