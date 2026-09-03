# 阶段 3 验收记录

验收日期：2026-09-02。

## 判定

当前判定：**PASS**。

确定性工程门禁、PostgreSQL 跨实例恢复和真实模型五故障在线门禁均已通过。最终在线验收使用数据集 `stage3-smoke-v2`、Prompt `hypotheses-v2` 和模型返回标识 `gpt-5.5`。

## 已通过

- Fake Model 确定性完成接入、范围判定、四源并行收集、Top-3 假设、引用校验、建议和报告。
- 未知 Evidence ID 触发结构化校验，并只允许一次修复调用。
- 模型 429/超时类故障只有限重试，达到模型调用预算后基于现有证据生成有限报告。
- Loki 等任一证据源失败转换为 `EvidenceGap`，其他源和报告继续。
- 模型调用、工具调用、Token、总时长和验证轮次均有上限；取消在每个外部调用阶段前检查。
- PostgreSQL 集成验收在 `VERIFYING` 后关闭第一套 graph/checkpointer 连接，用第二套实例自动认领并恢复；已经成功的四次工具调用没有重放。
- Checkpoint 仅保存 JSON 兼容状态，反序列化器禁用任意 msgpack 模块构造。
- 数据库或必要凭据缺失时拒绝创建调查，不退回不可审计的内存任务。
- Python 单元测试覆盖率不低于 90%。
- 真实模型 5/5 用例完成，每例输出 3 个根因候选；Ground Truth 概念组与证据引用全部通过。
- 首次 PASS 在线运行共执行 5 次模型调用、20 次只读工具调用，输入 2,809 Token、输出 2,262 Token；0 次结构修复、0 个 Evidence Gap。最终提交绑定报告保存在忽略提交的 `artifacts/stage3-online.json`。

## 架构评审结论

采用 LangGraph Graph API + PostgreSQL Checkpointer，但领域模型、预算和证据校验不依赖 LangGraph。另设轻量 PostgreSQL 调查表用于状态查询、取消和崩溃后的短租约认领，因为 checkpoint 本身不是业务任务队列。V1 保持固定单 Agent 图，不引入 ReAct、多 Agent、Temporal、Kafka 或 Redis；当前任务持续时间和并发规模不足以证明这些组件的成本合理。模型输出使用供应方原生严格 JSON Schema，之后仍执行本地 Pydantic 和 Evidence ID 校验；旧 `json_object` 模式仅保留为无 Schema 请求的兼容路径。

## 自动复现

```bash
make acceptance-stage3

# 重跑真实模型门禁
AI_SRE_MODEL_BASE_URL=https://provider.example/v1 \
AI_SRE_MODEL_API_KEY=... \
AI_SRE_MODEL_ID=... \
make eval-online
```

`evals/stage3-cases.json` 固定五个故障族冒烟用例。Ground Truth 使用预先版本化的概念组和同义词，每组至少命中一个，避免把 `2.5 seconds` 与 `2500 ms` 等价表达误判为失败。在线报告记录 Commit、数据集、供应方返回的实际模型标识、Top-3 陈述、概念组命中和引用合法性，但不会记录 API Key 或原始证据。

## 适用边界

- 阶段 3 只产生 `ProposedAction`，不执行变更；审批与隔离处置属于阶段 5。
- 五用例只证明模型适配和调查闭环可运行，不替代阶段 6 的 30+ 用例准确率、安全、成本和延迟正式评测。
- 短租约数据库 worker 满足 V1 单实例恢复；只有出现真实多实例争抢或跨天调查后才重新评估专用队列或 Temporal。
