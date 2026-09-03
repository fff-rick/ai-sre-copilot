import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type Evidence,
  type InvestigationEvent,
  type InvestigationSummary,
  type Status,
  type StoredInvestigation,
  getEvidence,
  getInvestigation,
  getTimeline,
  listInvestigations,
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
    COMPLETED: "调查完成",
    CANCELLED: "已取消",
    FAILED: "调查失败",
  };
  return labels[status];
}

export function App() {
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

  const refreshList = useCallback(async () => {
    const items = await listInvestigations();
    setInvestigations(items);
    return items;
  }, []);

  const refreshSelected = useCallback(async (id: string) => {
    const [record, events] = await Promise.all([
      getInvestigation(id),
      getTimeline(id),
    ]);
    setSelected(record);
    setTimeline(events);
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

      <div className="workspace">
        <aside className="investigation-list" aria-label="调查列表">
          <div className="panel-heading">
            <span>Investigations</span>
            <b>{investigations.length}</b>
          </div>
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
                <span className="status-mini">{statusLabel(item.status)}</span>
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
                            style={{ width: `${hypothesis.confidence * 100}%` }}
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
                    <p className="empty">调查正在进行，结论将在校验后写入。</p>
                  )}
                </section>
              </div>
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
    </div>
  );
}
