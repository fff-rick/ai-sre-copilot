# 工程基线

## 运行时

| 工具 | 基线 |
|---|---|
| Python | 3.14.x |
| uv | 0.12.x |
| Go | 1.26.x |
| Node.js | 24 LTS |
| pnpm | 10.x |
| PostgreSQL / pgvector | 18 / 0.8.6 |

补丁版本记录在仓库根目录的 `.tool-versions`，依赖精确版本分别记录在 `uv.lock`、`go.sum`（出现第三方依赖后）和 `pnpm-lock.yaml`。

Go 1.27 刚发布，阶段 0 暂用仍受支持且已经过补丁修复的 1.26 系列，待依赖和 CI 镜像验证后再升级。Python 3.15 尚处于预发布状态，不进入基线。

## 统一命令

```bash
make bootstrap
make lint
make test
make build
make compose-up
make compose-down
```

本地缺少某种运行时时，可直接使用 CI 或 Docker 构建验证。提交前至少运行 `make lint test`。

## 领域术语

| 术语 | 定义 |
|---|---|
| Investigation | 从一条告警开始、具有预算和终态的一次调查 |
| Evidence | 由已注册工具或受控文档产生、可引用且可追溯的事实材料 |
| Hypothesis | 可被证据支持或反驳的候选根因，不等同于事实 |
| Tool | 具有固定名称、版本化输入和确定错误语义的能力 |
| Mutation | 会改变外部系统状态、必须经审批的工具调用 |
| Artifact | 因体积或敏感性不进入普通上下文的受控原始结果 |

## 错误分类

| 代码 | 是否可重试 | 含义 |
|---|---:|---|
| `INVALID_ARGUMENT` | 否 | 输入不符合 Schema、时间窗或大小约束 |
| `UNAUTHENTICATED` | 否 | 缺少或无法验证身份 |
| `PERMISSION_DENIED` | 否 | 身份无权调用工具或操作目标 |
| `DEADLINE_EXCEEDED` | 是 | 在调用方 Deadline 前未完成 |
| `RATE_LIMITED` | 是 | 本地或上游限流 |
| `SOURCE_UNAVAILABLE` | 是 | 数据源暂时不可用 |
| `RESULT_TOO_LARGE` | 条件性 | 结果应裁剪或转为 Artifact |
| `CONFLICT` | 否 | 状态、审批绑定或幂等键冲突 |
| `INTERNAL` | 条件性 | 未分类内部错误，必须关联 Trace |

只有显式标记为可重试的错误才能自动重试，并且必须受次数和总时长预算限制。
