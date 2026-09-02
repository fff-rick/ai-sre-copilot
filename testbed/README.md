# Observable Testbed

阶段 1 测试床使用一个 Go 镜像运行四个业务角色，并使用独立 PostgreSQL：

```text
Client -> API -> Order -> Inventory -> PostgreSQL
                         -> Payment
```

所有 HTTP 客户端和服务端调用传播 W3C Trace Context。Trace 和 Metric 通过 OTLP HTTP 发送给 OpenTelemetry Collector；结构化 JSON 日志由 Collector 的 filelog receiver 读取，并保留 `trace_id` 和 `span_id`。cAdvisor 向 Prometheus 提供测试床容器的 CPU 与内存指标。

完整端到端验收结果见 [阶段 1 验收记录](../docs/08-stage1-validation.md)。

## 启动

```bash
make testbed-up
make testbed-smoke
```

访问入口：

- API：<http://localhost:18080>
- Grafana：<http://localhost:13000>
- Prometheus：<http://localhost:19090>
- Loki：<http://localhost:13100>
- Tempo：<http://localhost:13200>
- Collector Health：<http://localhost:13133>

停止环境但保留数据：

```bash
make testbed-down
```

## 故障注入

阶段 1 包含八个可重复场景：

```bash
./testbed/scripts/fault.sh inject latency-inventory
./testbed/scripts/fault.sh recover inventory
./testbed/scripts/fault.sh inject errors-payment
./testbed/scripts/fault.sh recover payment
./testbed/scripts/fault.sh inject cpu-order
./testbed/scripts/fault.sh recover order
./testbed/scripts/fault.sh inject memory-payment
./testbed/scripts/fault.sh recover payment
./testbed/scripts/fault.sh inject pool-inventory
./testbed/scripts/fault.sh recover inventory
./testbed/scripts/fault.sh inject dependency-payment
./testbed/scripts/fault.sh recover order
./testbed/scripts/fault.sh inject config-payment-path
./testbed/scripts/fault.sh recover order
./testbed/scripts/fault.sh inject release-payment
./testbed/scripts/fault.sh recover payment
```

使用 `make testbed-validate` 可依次注入、断言、恢复全部场景；该命令需要 Docker、curl 和 jq。故障默认持续 120 秒，服务端强制限制在 1～900 秒。每次注入与显式恢复都会追加记录到 `testbed/artifacts/fault-events/events.jsonl`，Ground Truth 位于 `testbed/scenarios/`。

CPU、内存和持续时间均有硬上限。Compose 为每个业务容器限制 0.5 CPU 和 192 MiB；内存场景只保留 64 MiB，不模拟 OOM。故障被替换、显式恢复或到期时，服务会停止负载并释放由故障管理器持有的资源。

故障控制接口只在 Compose 网络中可用，并要求 `X-Testbed-Control: stage1-local`。它是测试能力，不得复制到生产服务。

## 当前边界

资源场景用于生成有界压力和可验证证据，不用于容量评测。发布回归是带前后版本和输入条件的测试代码路径，不会替换真实镜像；真实 Kubernetes 发布和回滚属于后续阶段。
