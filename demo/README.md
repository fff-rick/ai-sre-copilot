# V1 演示

`run-stage7.sh` 固定演示以下路径：

1. 生成 32 用例冻结回放报告并供 Web 展示；
2. 启动可观测测试床和产品栈；
3. 注入 payment 100% 错误率并产生失败请求；
4. 创建调查，等待带证据报告并输出 Trace ID；
5. 提交隔离重启申请，验证未审批执行被 403 阻断，再拒绝申请；
6. 恢复故障并验证业务请求成功。

运行需要 Docker、curl、jq，以及有效的 `AI_SRE_MODEL_*` 环境变量：

```bash
./demo/run-stage7.sh
```

录制可审阅的 asciinema 文件：

```bash
./demo/record-stage7.sh

# 可选：将可审阅录制保存在 demo 目录
STAGE7_DEMO_RECORDING=demo/stage7-demo.cast ./demo/record-stage7.sh
```

脚本优先使用已安装的 `asciinema`，否则通过 `uvx asciinema` 运行，不向系统环境安装包。
仓库内附带一次真实模型、真实可观测数据的已验收录制，可直接回放：

```bash
asciinema play demo/stage7-demo.cast
# 未安装 asciinema 时
uvx asciinema play demo/stage7-demo.cast
```

脚本通过 `trap` 尝试恢复 payment 故障，不自动停止容器或删除数据卷。真正的 Kubernetes
restart、scale、rollback 使用 `make test-stage5-kind` 验证；默认 Compose 没有集群凭据，按
Fail Closed 原则拒绝实际变更。
