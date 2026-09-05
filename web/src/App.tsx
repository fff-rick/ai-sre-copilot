import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type Evidence,
  type EvaluationReport,
  type CreateInvestigationInput,
  type InvestigationEvent,
  type InvestigationSummary,
  type RemediationAction,
  type RemediationApproval,
  type RemediationExecution,
  type Status,
  type StoredInvestigation,
  createInvestigation,
  getEvidence,
  getInvestigation,
  getTimeline,
  getLatestEvaluation,
  listInvestigations,
  listApprovals,
  proposeApproval,
  approveApproval,
  rejectApproval,
  executeApproval,
  modifyApproval,
} from "./api";

import { Modal } from "./Modal";
import {
  actionLabels,
  approvalLabels,
  recoveryLabels,
  severityLabels,
  levelLabels,
  displayLabel,
  displayTime,
  statusLabel,
} from "./locale";

const terminal = new Set<Status>(["COMPLETED", "CANCELLED", "FAILED"]);

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function EvaluationDashboard({
  report,
  loadError,
}: {
  report: EvaluationReport | null;
  loadError: boolean;
}) {
  if (loadError || (report && report.profiles.length === 0)) {
    return (
      <main id="main-content" className="quality-dashboard">
        <section className="quality-empty" role="status">
          <p className="eyebrow">质量报告暂不可用</p>
          <h2>尚无可展示的质量报告</h2>
          <p>请联系管理员生成评估报告，然后重新打开质量报告。</p>
        </section>
      </main>
    );
  }
  if (!report) {
    return (
      <main id="main-content" className="quality-dashboard quality-loading">
        读取质量报告…
      </main>
    );
  }
  const candidate =
    report.profiles.find(
      (item) => item.prompt_version === report.gate_profile,
    ) ?? report.profiles.at(-1)!;
  const metrics = candidate.metrics;
  return (
    <main id="main-content" className="quality-dashboard">
      <section className="quality-hero">
        <div>
          <p className="eyebrow">质量评估 · {displayLabel(report.mode)}</p>
          <h2>{report.dataset}</h2>
          <p>
            提示词版本 {candidate.prompt_version} · 模型 {candidate.model_id}
          </p>
        </div>
        <span className={report.passed ? "gate-pass" : "gate-fail"}>
          {report.passed ? "评估通过" : "评估未通过"}
        </span>
      </section>
      <section className="metric-grid" aria-label="核心质量指标">
        {[
          ["完成率", percent(metrics.completion_rate)],
          ["首选根因准确率", percent(metrics.top1_accuracy)],
          ["前三根因命中率", percent(metrics.top3_accuracy)],
          ["证据引用有效率", percent(metrics.evidence_validity)],
          ["安全检查通过率", percent(metrics.security_pass_rate)],
          ["链路完整率", percent(metrics.trace_completeness)],
          ["95% 请求耗时", `${metrics.p95_duration_seconds.toFixed(3)} 秒`],
          ["95% 请求成本（美元）", `$${metrics.p95_cost_usd.toFixed(6)}`],
        ].map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>
      <section className="panel family-panel">
        <div className="panel-heading">
          <span>按故障类型统计</span>
          <b>{Object.keys(candidate.family_metrics).length}</b>
        </div>
        <div className="family-table" role="table" aria-label="按故障类型统计">
          <div className="family-row family-head" role="row">
            <span role="columnheader">故障类型</span>
            <span role="columnheader">用例</span>
            <span role="columnheader">首选准确率</span>
            <span role="columnheader">前三命中率</span>
          </div>
          {Object.entries(candidate.family_metrics).map(([family, item]) => (
            <div className="family-row" role="row" key={family}>
              <strong role="rowheader" title={family}>
                {displayLabel(family)}
              </strong>
              <span role="cell">{item.case_count}</span>
              <span role="cell">{percent(item.top1_accuracy)}</span>
              <span role="cell">{percent(item.top3_accuracy)}</span>
            </div>
          ))}
        </div>
      </section>
      <footer className="quality-provenance">
        数据集校验值 {report.dataset_sha256.slice(0, 16)} · 提示词校验值{" "}
        {candidate.prompt_sha256.slice(0, 16)} · 代码版本{" "}
        {report.commit.slice(0, 12)}
      </footer>
    </main>
  );
}

export function App() {
  const [view, setView] = useState<"investigations" | "quality">(
    "investigations",
  );
  const [evaluation, setEvaluation] = useState<EvaluationReport | null>(null);
  const [evaluationError, setEvaluationError] = useState(false);
  const [investigations, setInvestigations] = useState<InvestigationSummary[]>(
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const activeId = useRef<string | null>(null);
  const [selected, setSelected] = useState<StoredInvestigation | null>(null);
  const [timeline, setTimeline] = useState<InvestigationEvent[]>([]);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [connection, setConnection] = useState<
    "live" | "reconnecting" | "stored"
  >("stored");
  const [error, setError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<RemediationApproval[]>([]);
  const [approvalTokens, setApprovalTokens] = useState<Record<string, string>>(
    {},
  );
  const [executions, setExecutions] = useState<
    Record<string, RemediationExecution>
  >({});
  const [actionKind, setActionKind] = useState<
    "restart" | "scale" | "rollback"
  >("restart");
  const [actionValue, setActionValue] = useState(3);
  const [remediationBusy, setRemediationBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [listLoading, setListLoading] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newAlert, setNewAlert] = useState<CreateInvestigationInput>({
    service: "",
    severity: "critical",
    summary: "",
    sourceRef: "manual://web-console",
  });

  const refreshList = useCallback(async () => {
    const items = await listInvestigations();
    setInvestigations(items);
    return items;
  }, []);

  const refreshSelected = useCallback(async (id: string) => {
    const [record, events, approvalItems] = await Promise.all([
      getInvestigation(id),
      getTimeline(id),
      listApprovals(id),
    ]);
    if (activeId.current === id) {
      setSelected(record);
      setTimeline(events);
      setApprovals(approvalItems);
    }
    return record;
  }, []);

  useEffect(() => {
    void refreshList()
      .then((items) => {
        const requested = new URLSearchParams(window.location.search).get(
          "investigation",
        );
        const initial = items.find(
          (item) => item.investigation.investigation_id === requested,
        );
        setSelectedId(
          initial?.investigation.investigation_id ??
            items[0]?.investigation.investigation_id ??
            null,
        );
      })
      .catch(() => setError("无法读取调查列表，请稍后重试。"))
      .finally(() => setListLoading(false));
  }, [refreshList]);

  useEffect(() => {
    if (view !== "quality" || evaluation) return;
    setEvaluationError(false);
    void getLatestEvaluation()
      .then(setEvaluation)
      .catch(() => setEvaluationError(true));
  }, [evaluation, view]);

  useEffect(() => {
    activeId.current = selectedId;
    if (!selectedId) return;
    setConnection("stored");
    setEvidence(null);
    setError(null);
    const url = new URL(window.location.href);
    url.searchParams.set("investigation", selectedId);
    window.history.replaceState({}, "", url);

    let disposed = false;
    let source: EventSource | null = null;
    void refreshSelected(selectedId)
      .then((record) => {
        if (disposed) return;
        if (terminal.has(record.status)) {
          setConnection("stored");
          return;
        }
        source = new EventSource(
          `/api/v1/investigations/${encodeURIComponent(selectedId)}/events`,
        );
        source.onopen = () => setConnection("live");
        source.onerror = () => setConnection("reconnecting");
        source.addEventListener("investigation", () => {
          void Promise.all([refreshSelected(selectedId), refreshList()])
            .then(([updated]) => {
              if (disposed) return;
              if (terminal.has(updated.status)) {
                source?.close();
                setConnection("stored");
              }
            })
            .catch(() => {
              if (!disposed) setError("实时更新失败，请刷新页面重试。");
            });
        });
      })
      .catch(() => {
        if (!disposed) setError("无法读取调查详情，请刷新页面重试。");
      });

    return () => {
      disposed = true;
      activeId.current = null;
      source?.close();
    };
  }, [refreshList, refreshSelected, selectedId]);

  const evidenceById = useMemo(
    () =>
      new Map(
        selected?.report?.evidence.map((item) => [item.evidence_id, item]) ??
          [],
      ),
    [selected],
  );

  const openEvidence = async (evidenceId: string) => {
    if (!selectedId) return;
    const cached = evidenceById.get(evidenceId);
    if (cached) {
      setEvidence(cached);
      return;
    }
    try {
      setEvidence(await getEvidence(selectedId, evidenceId));
    } catch {
      setError("证据片段不可用。引用可能已过期。");
    }
  };

  const currentAction = useCallback((): RemediationAction | null => {
    if (!selected) return null;
    const service = selected.investigation.alert.service;
    const base: RemediationAction = {
      action_id: `act-${actionKind}-${service}`,
      tool_name: `kubernetes.${actionKind}_deployment`,
      namespace: "ai-sre-test",
      name: service,
      description: `${actionLabels[`kubernetes.${actionKind}_deployment`]}：${service}（隔离环境）`,
      expected_effect: "降低当前告警对应的错误指标。",
      rollback_plan: "停止后续动作并恢复上一版本或副本配置。",
      evidence_ids:
        selected.report?.hypotheses[0]?.supporting_evidence_ids ?? [],
      verification_promql: `sum(rate(testbed_http_server_requests_total{service_name="${service}",http_response_status_code=~"5.."}[5m]))`,
      recovery_goal: "decrease",
    };
    if (actionKind === "scale") base.replicas = actionValue;
    if (actionKind === "rollback") base.revision = actionValue;
    return base;
  }, [actionKind, actionValue, selected]);

  const performRemediation = async (operation: () => Promise<unknown>) => {
    if (!selectedId) return;
    setRemediationBusy(true);
    setError(null);
    try {
      await operation();
      await Promise.all([refreshSelected(selectedId), refreshList()]);
    } catch {
      setError("变更操作被拒绝或执行失败，请检查审批状态与参数。 ");
    } finally {
      setRemediationBusy(false);
    }
  };

  const filteredInvestigations = investigations.filter((item) => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    const alert = item.investigation.alert;
    const matchesSearch =
      `${alert.service} ${alert.summary} ${item.investigation.investigation_id}`
        .toLocaleLowerCase("zh-CN")
        .includes(query);
    const matchesStatus =
      statusFilter === "all" ||
      (statusFilter === "active"
        ? !terminal.has(item.status)
        : item.status === statusFilter);
    return matchesSearch && matchesStatus;
  });

  const reloadList = async () => {
    setListLoading(true);
    setError(null);
    try {
      await refreshList();
    } catch {
      setError("无法读取调查列表，请稍后重试。");
    } finally {
      setListLoading(false);
    }
  };

  const submitInvestigation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newAlert.service.trim() || !newAlert.summary.trim()) {
      setFormError("请填写服务名称和告警摘要，不能只输入空格。");
      return;
    }
    setCreating(true);
    setFormError(null);
    try {
      const created = await createInvestigation({
        ...newAlert,
        service: newAlert.service.trim(),
        summary: newAlert.summary.trim(),
      });
      setInvestigations((items) => [
        created,
        ...items.filter(
          (item) =>
            item.investigation.investigation_id !==
            created.investigation.investigation_id,
        ),
      ]);
      setSelectedId(created.investigation.investigation_id);
      setCreateOpen(false);
      setSearch("");
      setStatusFilter("all");
      setNewAlert((current) => ({ ...current, summary: "" }));
    } catch {
      setFormError("创建失败，请检查服务连接后重试。已填写的内容会保留。");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">
        跳转到主要内容
      </a>
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <div>
          <p className="eyebrow">AI-SRE Copilot</p>
          <h1>智能运维工作台</h1>
        </div>
        <nav className="topnav" aria-label="工作台导航">
          <button
            className={view === "investigations" ? "is-active" : ""}
            type="button"
            aria-current={view === "investigations" ? "page" : undefined}
            onClick={() => setView("investigations")}
          >
            故障调查
          </button>
          <button
            className={view === "quality" ? "is-active" : ""}
            type="button"
            aria-current={view === "quality" ? "page" : undefined}
            onClick={() => setView("quality")}
          >
            质量报告
          </button>
        </nav>
        <div className={`connection connection--${connection}`} role="status">
          <span aria-hidden="true" />
          {connection === "live"
            ? "实时同步"
            : connection === "reconnecting"
              ? "正在重连"
              : "历史记录"}
        </div>
        <span className="timezone">北京时间 UTC+8</span>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      {view === "quality" ? (
        <EvaluationDashboard report={evaluation} loadError={evaluationError} />
      ) : (
        <>
          <section className="page-heading">
            <div>
              <p className="breadcrumb">运维控制台 / 故障调查</p>
              <h2>故障调查</h2>
              <p>从告警到根因，让每一步判断都有据可查。</p>
            </div>
            <div className="page-actions">
              <button
                type="button"
                onClick={() => void reloadList()}
                disabled={listLoading}
              >
                {listLoading ? "刷新中…" : "刷新列表"}
              </button>
              <button
                className="primary"
                type="button"
                onClick={() => {
                  setFormError(null);
                  setCreateOpen(true);
                }}
              >
                ＋ 新建调查
              </button>
            </div>
          </section>
          <section className="overview-grid" aria-label="调查概览">
            {[
              ["最近调查", investigations.length, "最近 100 条记录"],
              [
                "进行中",
                investigations.filter((item) => !terminal.has(item.status))
                  .length,
                "持续追踪调查进展",
              ],
              [
                "等待审批",
                investigations.filter(
                  (item) => item.status === "WAITING_APPROVAL",
                ).length,
                "变更前需人工确认",
              ],
              [
                "调查完成",
                investigations.filter((item) => item.status === "COMPLETED")
                  .length,
                "根因与证据可回溯",
              ],
            ].map(([label, count, note]) => (
              <article key={label}>
                <span>{label}</span>
                <strong>{listLoading || error ? "—" : count}</strong>
                <small>{note}</small>
              </article>
            ))}
          </section>
          <div className="workspace">
            <aside className="investigation-list" aria-label="调查列表">
              <div className="panel-heading">
                <span>调查列表</span>
                <b>{investigations.length}</b>
              </div>
              <div className="list-tools">
                <input
                  type="search"
                  aria-label="搜索调查"
                  placeholder="搜索服务、告警或调查编号"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
                <select
                  aria-label="筛选调查状态"
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  <option value="all">全部状态</option>
                  <option value="active">进行中</option>
                  <option value="WAITING_APPROVAL">等待审批</option>
                  <option value="COMPLETED">调查完成</option>
                  <option value="FAILED">调查失败</option>
                  <option value="CANCELLED">已取消</option>
                </select>
                <span className="list-count" role="status">
                  {listLoading
                    ? "正在加载…"
                    : `共 ${filteredInvestigations.length} 条结果`}
                </span>
              </div>
              <div className="investigation-items">
                {filteredInvestigations.map((item) => {
                  const id = item.investigation.investigation_id;
                  return (
                    <button
                      className={
                        id === selectedId
                          ? "investigation-card is-active"
                          : "investigation-card"
                      }
                      aria-pressed={id === selectedId}
                      key={id}
                      onClick={() => setSelectedId(id)}
                      type="button"
                    >
                      <span
                        className={`severity severity--${item.investigation.alert.severity}`}
                      />
                      <span className="card-copy">
                        <strong>{item.investigation.alert.service}</strong>
                        <span
                          className={`severity-text severity-text--${item.investigation.alert.severity}`}
                        >
                          {severityLabels[item.investigation.alert.severity]}
                        </span>
                        <small>{item.investigation.alert.summary}</small>
                        <em>{displayTime(item.investigation.created_at)}</em>
                      </span>
                      <span className="status-mini">
                        {statusLabel(item.status)}
                      </span>
                    </button>
                  );
                })}
                {!filteredInvestigations.length && !error && !listLoading && (
                  <p className="empty">
                    {investigations.length
                      ? "没有匹配的调查，请调整关键词或筛选条件。"
                      : "暂无调查记录，点击「新建调查」开始排查。"}
                  </p>
                )}
              </div>
            </aside>

            <main className="investigation-detail" id="main-content">
              {selected &&
              selected.investigation.investigation_id === selectedId ? (
                <>
                  <section className="incident-header">
                    <div>
                      <div className="incident-meta">
                        <span
                          className={`severity-tag severity-tag--${selected.investigation.alert.severity}`}
                        >
                          {
                            severityLabels[
                              selected.investigation.alert.severity
                            ]
                          }
                        </span>
                        <code>{selected.investigation.alert.alert_id}</code>
                      </div>
                      <h2>{selected.investigation.alert.summary}</h2>
                      <p>
                        服务：{selected.investigation.alert.service} ·
                        链路编号： {selected.investigation.trace_id}
                      </p>
                    </div>
                    <div
                      className={`status-pill status-pill--${selected.status.toLowerCase()}`}
                    >
                      {statusLabel(selected.status)}
                    </div>
                  </section>

                  <div className="detail-grid">
                    <section className="panel timeline-panel">
                      <div className="panel-heading">
                        <span>调查时间线</span>
                        <b>{timeline.length}</b>
                      </div>
                      <p className="section-note">时间均为北京时间（UTC+8）</p>
                      <ol className="timeline">
                        {timeline.map((event) => (
                          <li key={event.event_id}>
                            <span className="timeline-dot" aria-hidden="true" />
                            <div>
                              <strong>
                                {event.payload.node
                                  ? statusLabel(event.status)
                                  : "收到告警"}
                              </strong>
                              <p title={event.event_type}>
                                {displayLabel(event.event_type)}
                              </p>
                              <small>{displayTime(event.created_at)}</small>
                            </div>
                            {event.payload.evidence_count !== undefined && (
                              <em>{event.payload.evidence_count} 份证据</em>
                            )}
                          </li>
                        ))}
                      </ol>
                    </section>

                    <section className="panel hypotheses-panel">
                      <div className="panel-heading">
                        <span>根因假设</span>
                        <b>{selected.report?.hypotheses.length ?? 0}</b>
                      </div>
                      {selected.report?.hypotheses.map((hypothesis) => (
                        <article
                          className="hypothesis"
                          key={hypothesis.hypothesis_id}
                        >
                          <div className="hypothesis-rank">
                            {String(hypothesis.rank).padStart(2, "0")}
                          </div>
                          <div className="hypothesis-body">
                            <div className="confidence-row">
                              <span>
                                {displayLabel(hypothesis.verification_status)}
                              </span>
                              <strong>
                                置信度 {Math.round(hypothesis.confidence * 100)}
                                %
                              </strong>
                            </div>
                            <div className="confidence-track">
                              <i
                                style={{
                                  width: `${hypothesis.confidence * 100}%`,
                                }}
                              />
                            </div>
                            <h3>{hypothesis.statement}</h3>
                            <div className="citations">
                              {hypothesis.supporting_evidence_ids.map((id) => (
                                <button
                                  type="button"
                                  aria-pressed={id === selectedId}
                                  key={id}
                                  onClick={() => void openEvidence(id)}
                                >
                                  {id}
                                </button>
                              ))}
                            </div>
                          </div>
                        </article>
                      ))}
                      {!selected.report && (
                        <p className="empty">
                          调查正在进行，结论将在校验后写入。
                        </p>
                      )}
                    </section>
                  </div>

                  {selected.report && (
                    <section
                      className="panel remediation-panel"
                      aria-label="变更审批"
                    >
                      <div className="panel-heading">
                        <span>变更审批</span>
                        <b>{approvals.length}</b>
                      </div>
                      <p className="section-note">
                        操作范围：隔离环境
                        ai-sre-test。提交申请后，需批准再执行。
                      </p>
                      <div className="remediation-compose">
                        <label>
                          动作
                          <select
                            value={actionKind}
                            onChange={(event) =>
                              setActionKind(
                                event.target.value as
                                  "restart" | "scale" | "rollback",
                              )
                            }
                          >
                            <option value="restart">重启工作负载</option>
                            <option value="scale">调整副本数</option>
                            <option value="rollback">回滚版本</option>
                          </select>
                        </label>
                        {actionKind !== "restart" && (
                          <label>
                            {actionKind === "scale"
                              ? "副本数"
                              : "版本号（0=上一版）"}
                            <input
                              type="number"
                              min="0"
                              max={actionKind === "scale" ? 100 : undefined}
                              value={actionValue}
                              onChange={(event) =>
                                setActionValue(Number(event.target.value))
                              }
                            />
                          </label>
                        )}
                        <button
                          type="button"
                          disabled={remediationBusy}
                          onClick={() => {
                            const candidate = currentAction();
                            if (candidate && selectedId) {
                              void performRemediation(() =>
                                proposeApproval(selectedId, candidate),
                              );
                            }
                          }}
                        >
                          提交审批
                        </button>
                      </div>
                      <div className="approval-list">
                        {approvals.map((approval) => (
                          <article
                            className="approval-card"
                            key={approval.approval_id}
                          >
                            <div>
                              <strong>
                                {actionLabels[approval.action.tool_name]}
                              </strong>
                              <code>{approval.target}</code>
                              <small>
                                {approvalLabels[approval.status]} ·{" "}
                                {levelLabels[approval.risk_level]}风险 ·
                                参数校验值{" "}
                                {approval.parameters_hash.slice(0, 12)}
                              </small>
                            </div>
                            <div className="approval-actions">
                              {approval.status === "PENDING" && (
                                <button
                                  type="button"
                                  disabled={remediationBusy}
                                  onClick={() =>
                                    void performRemediation(async () => {
                                      if (!selectedId) return;
                                      const grant = await approveApproval(
                                        selectedId,
                                        approval.approval_id,
                                      );
                                      setApprovalTokens((current) => ({
                                        ...current,
                                        [approval.approval_id]:
                                          grant.approval_token,
                                      }));
                                    })
                                  }
                                >
                                  批准
                                </button>
                              )}
                              {approval.status === "APPROVED" && (
                                <button
                                  type="button"
                                  disabled={
                                    remediationBusy ||
                                    !approvalTokens[approval.approval_id]
                                  }
                                  onClick={() =>
                                    void performRemediation(async () => {
                                      if (!selectedId) return;
                                      const result = await executeApproval(
                                        selectedId,
                                        approval.approval_id,
                                        approvalTokens[approval.approval_id]!,
                                        `console:${approval.approval_id}`,
                                      );
                                      setExecutions((current) => ({
                                        ...current,
                                        [approval.approval_id]: result,
                                      }));
                                    })
                                  }
                                >
                                  执行并验证
                                </button>
                              )}
                              {(approval.status === "PENDING" ||
                                approval.status === "APPROVED") && (
                                <>
                                  <button
                                    type="button"
                                    disabled={remediationBusy}
                                    onClick={() => {
                                      const candidate = currentAction();
                                      if (candidate && selectedId) {
                                        void performRemediation(() =>
                                          modifyApproval(
                                            selectedId,
                                            approval.approval_id,
                                            candidate,
                                          ),
                                        );
                                      }
                                    }}
                                  >
                                    更新参数
                                  </button>
                                  <button
                                    className="is-danger"
                                    type="button"
                                    disabled={remediationBusy}
                                    onClick={() =>
                                      selectedId &&
                                      void performRemediation(() =>
                                        rejectApproval(
                                          selectedId,
                                          approval.approval_id,
                                        ),
                                      )
                                    }
                                  >
                                    拒绝
                                  </button>
                                </>
                              )}
                            </div>
                            {executions[approval.approval_id] && (
                              <p className="recovery-result">
                                恢复判定 ·{" "}
                                {
                                  recoveryLabels[
                                    executions[approval.approval_id]!
                                      .recovery_status
                                  ]
                                }
                              </p>
                            )}
                          </article>
                        ))}
                        {!approvals.length && (
                          <p className="empty">
                            尚无变更申请。所有操作都需要独立审批。
                          </p>
                        )}
                      </div>
                    </section>
                  )}
                </>
              ) : (
                <section className="welcome-state">
                  <span>+</span>
                  <h2>
                    {error
                      ? "调查数据暂不可用"
                      : selectedId
                        ? "正在加载调查详情…"
                        : "从一条告警开始排查"}
                  </h2>
                  <p>
                    {error
                      ? "请检查服务连接后刷新重试。"
                      : selectedId
                        ? "正在读取时间线、分析结论和相关证据。"
                        : "新建调查，或从左侧选择一条记录查看详情。"}
                  </p>
                </section>
              )}
            </main>

            {evidence && (
              <Modal title="证据详情" drawer onClose={() => setEvidence(null)}>
                <p className="eyebrow">原始证据 · 可追溯</p>
                <h2>{displayLabel(evidence.source_type)}</h2>
                <div className="evidence-badges">
                  <span>可信度：{levelLabels[evidence.reliability]}</span>
                  <code>{evidence.evidence_id}</code>
                </div>
                <dl>
                  <dt>来源</dt>
                  <dd>{evidence.source_ref}</dd>
                  <dt>查询</dt>
                  <dd>
                    <pre>{JSON.stringify(evidence.query, null, 2)}</pre>
                  </dd>
                  <dt>观测时间（北京时间）</dt>
                  <dd>{displayTime(evidence.observed_at)}</dd>
                </dl>
                <h3>证据片段</h3>
                <blockquote>{evidence.content_excerpt}</blockquote>
                <p className="hash">SHA-256 · {evidence.content_hash}</p>
              </Modal>
            )}
          </div>
          {createOpen && (
            <Modal
              title="新建调查"
              onClose={() => {
                if (!creating) setCreateOpen(false);
              }}
            >
              <form
                className="create-investigation"
                onSubmit={submitInvestigation}
              >
                <h2>新建调查</h2>
                <p className="form-hint">
                  填写告警信息，自动分析最近 10 分钟的运行数据。
                </p>
                {formError && (
                  <p className="form-error" role="alert">
                    {formError}
                  </p>
                )}
                <label>
                  服务
                  <input
                    required
                    maxLength={64}
                    placeholder="例如：payment、order-service"
                    value={newAlert.service}
                    onChange={(event) =>
                      setNewAlert((current) => ({
                        ...current,
                        service: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  严重度
                  <select
                    value={newAlert.severity}
                    onChange={(event) =>
                      setNewAlert((current) => ({
                        ...current,
                        severity: event.target.value as
                          "critical" | "warning" | "info",
                      }))
                    }
                  >
                    <option value="critical">严重 · 核心业务不可用</option>
                    <option value="warning">警告 · 服务异常或性能下降</option>
                    <option value="info">提示 · 需要关注</option>
                  </select>
                </label>
                <label>
                  告警摘要
                  <textarea
                    required
                    maxLength={500}
                    rows={4}
                    placeholder="例如：支付服务错误率持续升高，部分用户支付超时"
                    value={newAlert.summary}
                    onChange={(event) =>
                      setNewAlert((current) => ({
                        ...current,
                        summary: event.target.value,
                      }))
                    }
                  />
                </label>
                <small className="form-hint">
                  {newAlert.summary.length}/500 字 · 时间范围：最近 10 分钟
                </small>
                <div className="modal-actions">
                  <button
                    type="button"
                    disabled={creating}
                    onClick={() => setCreateOpen(false)}
                  >
                    取消
                  </button>
                  <button className="primary" type="submit" disabled={creating}>
                    {creating ? "创建中…" : "创建调查"}
                  </button>
                </div>
              </form>
            </Modal>
          )}
        </>
      )}
    </div>
  );
}
