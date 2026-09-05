import type { Status } from "./api";

const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function displayTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : dateFormat.format(date);
}

export const severityLabels = {
  critical: "严重",
  warning: "警告",
  info: "提示",
};
export const levelLabels = { high: "高", medium: "中", low: "低" };
export const actionLabels = {
  "kubernetes.restart_deployment": "重启工作负载",
  "kubernetes.scale_deployment": "调整副本数",
  "kubernetes.rollback_deployment": "回滚版本",
};
export const approvalLabels = {
  PENDING: "待审批",
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  EXPIRED: "已过期",
  CONSUMED: "已执行",
};
export const recoveryLabels = {
  RECOVERED: "已恢复",
  NOT_RECOVERED: "尚未恢复",
  UNABLE_TO_DETERMINE: "暂无法判定",
};
export function statusLabel(status: Status) {
  const labels: Record<Status, string> = {
    RECEIVED: "已接收",
    SCOPING: "确认影响范围",
    COLLECTING: "采集证据",
    HYPOTHESIZING: "分析可能根因",
    VERIFYING: "验证根因假设",
    RECOMMENDING: "生成处置建议",
    REPORTING: "生成报告",
    WAITING_APPROVAL: "等待审批",
    EXECUTING: "执行变更",
    VALIDATING: "验证恢复",
    COMPLETED: "调查完成",
    CANCELLED: "已取消",
    FAILED: "调查失败",
  };
  return labels[status] ?? status;
}

// Only translate known display values; keep identifiers and original evidence intact.
export function displayLabel(value: string) {
  const labels: Record<string, string> = {
    supported: "证据支持",
    contradicted: "证据不支持",
    unverified: "待验证",
    inconclusive: "尚无定论",
    insufficient_evidence: "证据不足",
    "knowledge.runbook": "运维手册",
    "knowledge.postmortem": "故障复盘",
    "knowledge.sop": "操作规程",
    knowledge: "知识库",
    model: "模型分析",
    "prometheus.query": "监控指标",
    "loki.query": "日志查询",
    "tempo.query": "链路追踪",
    database: "数据库故障",
    capacity: "容量不足",
    configuration: "配置异常",
    network: "网络故障",
    release: "发布变更",
    resource: "资源异常",
    kubernetes: "容器编排故障",
    security: "安全风险",
    latency: "响应延迟",
    error_rate: "错误率",
    saturation: "资源饱和",
    dependency: "依赖故障",
    replay: "回放评估",
    live: "在线评估",
    offline: "离线评估",
    "investigation.created": "告警已接收，开始调查",
    "investigation.finished": "调查结束，结果已保存",
  };
  if (labels[value]) return labels[value];
  if (value.startsWith("node.") && value.endsWith(".completed"))
    return "本阶段处理完成";
  return value;
}
