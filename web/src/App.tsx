import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
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

const terminal = new Set<Status>(["COMPLETED", "CANCELLED", "FAILED"]);

function displayTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function statusLabel(status: Status) {
  const labels: Record<Status, string> = {
    RECEIVED: "已接收",
    SCOPING: "范围判定",
    COLLECTING: "采集证据",
    HYPOTHESIZING: "生成假设",
    VERIFYING: "验证假设",
    RECOMMENDING: "形成建议",
    REPORTING: "生成报告",
    WAITING_APPROVAL: "等待审批",
    EXECUTING: "执行变更",
    VALIDATING: "验证恢复",
    COMPLETED: "调查完成",
    CANCELLED: "已取消",
    FAILED: "调查失败",
  };
  return labels[status];
}

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
  if (loadError) {
    return (
      <main className="quality-dashboard">
        <section className="quality-empty" role="status">
          <p className="eyebrow">Evaluation unavailable</p>
          <h2>尚无可展示的质量报告</h2>
          <p>
            先运行 <code>make eval-offline</code>，再刷新本页面。
          </p>
        </section>
      </main>
    );
  }
  if (!report) {
    return (
      <main className="quality-dashboard quality-loading">读取质量报告…</main>
    );
  }
  const candidate =
    report.profiles.find(
      (item) => item.prompt_version === report.gate_profile,
    ) ?? report.profiles.at(-1)!;
  const metrics = candidate.metrics;
  return (
    <main className="quality-dashboard">
      <section className="quality-hero">
        <div>
          <p className="eyebrow">Frozen quality gate · {report.mode}</p>
          <h2>{report.dataset}</h2>
          <p>
            Prompt {candidate.prompt_version} · Model {candidate.model_id}
          </p>
        </div>
        <span className={report.passed ? "gate-pass" : "gate-fail"}>
          {report.passed ? "PASS" : "FAIL"}
        </span>
      </section>
      <section className="metric-grid" aria-label="核心质量指标">
        {[
          ["完成率", percent(metrics.completion_rate)],
          ["Top-1", percent(metrics.top1_accuracy)],
          ["Top-3", percent(metrics.top3_accuracy)],
          ["有效引用", percent(metrics.evidence_validity)],
          ["安全通过", percent(metrics.security_pass_rate)],
          ["Trace 完整", percent(metrics.trace_completeness)],
          ["P95 时长", `${metrics.p95_duration_seconds.toFixed(3)}s`],
          ["P95 成本", `$${metrics.p95_cost_usd.toFixed(6)}`],
        ].map(([label, value]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>
      <section className="panel family-panel">
        <div className="panel-heading">
          <span>按故障族拆分</span>
          <b>{Object.keys(candidate.family_metrics).length}</b>
        </div>
        <div className="family-table" role="table">
          <div className="family-row family-head" role="row">
            <span>故障族</span>
            <span>用例</span>
            <span>Top-1</span>
            <span>Top-3</span>
          </div>
          {Object.entries(candidate.family_metrics).map(([family, item]) => (
            <div className="family-row" role="row" key={family}>
              <strong>{family}</strong>
              <span>{item.case_count}</span>
              <span>{percent(item.top1_accuracy)}</span>
              <span>{percent(item.top3_accuracy)}</span>
            </div>
          ))}
        </div>
      </section>
      <footer className="quality-provenance">
        Dataset SHA {report.dataset_sha256.slice(0, 16)} · Prompt SHA{" "}
        {candidate.prompt_sha256.slice(0, 16)} · Commit{" "}
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
  const [creating, setCreating] = useState(false);
  const [newAlert, setNewAlert] = useState<CreateInvestigationInput>({
    service: "payment",
    severity: "critical",
    summary: "Payment error rate increased",
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
    setSelected(record);
    setTimeline(events);
    setApprovals(approvalItems);
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
      .catch(() => setError("无法读取调查列表，请稍后重试。"));
  }, [refreshList]);

  useEffect(() => {
    if (view !== "quality" || evaluation) return;
    setEvaluationError(false);
    void getLatestEvaluation()
      .then(setEvaluation)
      .catch(() => setEvaluationError(true));
  }, [evaluation, view]);

  useEffect(() => {
    if (!selectedId) return;
    setEvidence(null);
    setError(null);
    const url = new URL(window.location.href);
    url.searchParams.set("investigation", selectedId);
    window.history.replaceState({}, "", url);

    let disposed = false;
    let source: EventSource | null = null;
    void refreshSelected(selectedId)
      .then((record) => {
        if (disposed || terminal.has(record.status)) {
          setConnection("stored");
          return;
        }
        source = new EventSource(
          `/api/v1/investigations/${encodeURIComponent(selectedId)}/events`,
        );
        source.onopen = () => setConnection("live");
        source.onerror = () => setConnection("reconnecting");
        source.addEventListener("investigation", () => {
          void Promise.all([refreshSelected(selectedId), refreshList()]).then(
            ([updated]) => {
              if (terminal.has(updated.status)) {
                source?.close();
                setConnection("stored");
              }
            },
          );
        });
      })
      .catch(() => setError("无法读取调查详情。"));

    return () => {
      disposed = true;
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
      description: `${actionKind} isolated ${service} Deployment`,
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

  const submitInvestigation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const created = await createInvestigation(newAlert);
      await refreshList();
      setSelectedId(created.investigation.investigation_id);
    } catch {
      setError("无法创建调查，请确认模型与持久化运行时已配置。");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand-mark" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <div>
          <p className="eyebrow">AI-SRE · Evidence workspace</p>
          <h1>故障调查台</h1>
        </div>
        <nav className="topnav" aria-label="工作台导航">
          <button
            className={view === "investigations" ? "is-active" : ""}
            type="button"
            onClick={() => setView("investigations")}
          >
            调查
          </button>
          <button
            className={view === "quality" ? "is-active" : ""}
            type="button"
            onClick={() => setView("quality")}
          >
            质量报告
          </button>
        </nav>
        <div className={`connection connection--${connection}`}>
          <span aria-hidden="true" />
          {connection === "live"
            ? "实时同步"
            : connection === "reconnecting"
              ? "正在重连"
              : "持久快照"}
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      {view === "quality" ? (
        <EvaluationDashboard report={evaluation} loadError={evaluationError} />
      ) : (
        <div className="workspace">
          <aside className="investigation-list" aria-label="调查列表">
            <div className="panel-heading">
              <span>Investigations</span>
              <b>{investigations.length}</b>
            </div>
            <form
              className="create-investigation"
              onSubmit={submitInvestigation}
            >
              <strong>新建调查</strong>
              <label>
                服务
                <input
                  required
                  maxLength={64}
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
                  <option value="critical">critical</option>
                  <option value="warning">warning</option>
                  <option value="info">info</option>
                </select>
              </label>
              <label>
                告警摘要
                <textarea
                  required
                  maxLength={500}
                  value={newAlert.summary}
                  onChange={(event) =>
                    setNewAlert((current) => ({
                      ...current,
                      summary: event.target.value,
                    }))
                  }
                />
              </label>
              <button type="submit" disabled={creating}>
                {creating ? "创建中…" : "创建调查"}
              </button>
            </form>
            {investigations.map((item) => {
              const id = item.investigation.investigation_id;
              return (
                <button
                  className={
                    id === selectedId
                      ? "investigation-card is-active"
                      : "investigation-card"
                  }
                  key={id}
                  onClick={() => setSelectedId(id)}
                  type="button"
                >
                  <span
                    className={`severity severity--${item.investigation.alert.severity}`}
                  />
                  <span className="card-copy">
                    <strong>{item.investigation.alert.service}</strong>
                    <small>{item.investigation.alert.summary}</small>
                    <em>{displayTime(item.investigation.created_at)}</em>
                  </span>
                  <span className="status-mini">
                    {statusLabel(item.status)}
                  </span>
                </button>
              );
            })}
            {!investigations.length && !error && (
              <p className="empty">暂无调查记录</p>
            )}
          </aside>

          <main className="investigation-detail">
            {selected ? (
              <>
                <section className="incident-header">
                  <div>
                    <div className="incident-meta">
                      <span
                        className={`severity-tag severity-tag--${selected.investigation.alert.severity}`}
                      >
                        {selected.investigation.alert.severity}
                      </span>
                      <code>{selected.investigation.alert.alert_id}</code>
                    </div>
                    <h2>{selected.investigation.alert.summary}</h2>
                    <p>
                      {selected.investigation.alert.service} · Trace{" "}
                      {selected.investigation.trace_id}
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
                            <p>{event.event_type}</p>
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
                            <span>{hypothesis.verification_status}</span>
                            <strong>
                              {Math.round(hypothesis.confidence * 100)}%
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
                      <span>受控变更 · isolated only</span>
                      <b>{approvals.length}</b>
                    </div>
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
                          <option value="restart">重启 Deployment</option>
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
                            <strong>{approval.action.tool_name}</strong>
                            <code>{approval.target}</code>
                            <small>
                              {approval.status} · {approval.risk_level} risk ·
                              hash {approval.parameters_hash.slice(0, 12)}
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
                                executions[approval.approval_id]!
                                  .recovery_status
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
                <h2>选择一条调查</h2>
                <p>查看可恢复的时间线、根因假设与原始证据。</p>
              </section>
            )}
          </main>

          {evidence && (
            <aside className="evidence-drawer" aria-label="证据详情">
              <button
                className="drawer-close"
                type="button"
                onClick={() => setEvidence(null)}
                aria-label="关闭证据详情"
              >
                ×
              </button>
              <p className="eyebrow">Evidence detail</p>
              <h2>{evidence.source_type}</h2>
              <div className="evidence-badges">
                <span>{evidence.reliability} reliability</span>
                <code>{evidence.evidence_id}</code>
              </div>
              <dl>
                <dt>来源</dt>
                <dd>{evidence.source_ref}</dd>
                <dt>查询</dt>
                <dd>
                  <pre>{JSON.stringify(evidence.query, null, 2)}</pre>
                </dd>
                <dt>观测时间</dt>
                <dd>{displayTime(evidence.observed_at)}</dd>
              </dl>
              <h3>证据片段</h3>
              <blockquote>{evidence.content_excerpt}</blockquote>
              <p className="hash">SHA-256 · {evidence.content_hash}</p>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
