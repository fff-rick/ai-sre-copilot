# 阶段 7 验收记录

## 范围

阶段 7 完成有界并发、单源故障降级、容器资源限制、质量报告 Web 页面、浏览器创建调查、发布
来源绑定、敏感模式扫描、API/部署/威胁模型文档和可录制演示脚本。

## 自动门禁

```bash
make acceptance-stage7
```

该命令组合阶段 6 全门禁、PostgreSQL 恢复、pgvector/SSE、检索评测、kind 隔离变更和阶段 7
报告。快速复查阶段 7 自身能力：

```bash
make eval-offline
make eval-retrieval
make verify-stage7
make release-manifest
```

输出位于：

- `artifacts/stage7-acceptance.{json,md}`；
- `artifacts/release-manifest.json`；
- `artifacts/stage6-report.{json,md}`；
- `artifacts/stage4-retrieval.{json,md}`。

## 已验证结果

- 五个调查全部进入 `COMPLETED`，观测到 5 个并行模型调用，停止后无 worker task 残留；
- Loki 主动失败时调查仍完成，并写入明确 evidence gap；
- 阶段 6 candidate 的完成率、Top-1、Top-3、证据、安全和 Trace 指标均为 100%；
- 未支持陈述率 0%，确定性 P95 小于 1 秒；
- 四个产品容器均具有 CPU、内存、PID 和禁止提权限制；
- Web 可创建调查并查看按故障族拆分的脱敏评测报告；
- `demo/stage7-demo.cast` 已记录真实模型、Prometheus、Loki、Tempo、审批阻断和故障恢复路径，
  可用 `uvx asciinema play demo/stage7-demo.cast` 回放；
- 跟踪文件的私钥/API Key 模式扫描通过；已知测试假密钥被显式 allowlist；
- 基线 Prompt 保留 8 个 Top-1 推理失败，Web/Markdown 不隐藏失败对比。

## 发布边界

上述结果是冻结回放和本机回归，不代表生产容量。正式 `v*` Tag 工作流必须拥有真实模型端点、
密钥、模型 ID 和输入/输出价格，重新执行 32 用例在线评测并通过后才创建 GitHub Release 和
provenance attestation。Tag 只能在本 PR 合并后创建。
