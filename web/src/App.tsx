const principles = [
  ["Evidence First", "关键结论必须能回到原始证据。"],
  ["Read-only by Default", "首阶段不开放任何状态变更工具。"],
  ["Bounded Autonomy", "调查受状态机、预算和审批约束。"],
] as const;

export function App() {
  return (
    <main>
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Stage 0 · Engineering baseline</p>
        <h1 id="page-title">AI-SRE Copilot</h1>
        <p className="summary">
          面向故障调查的证据工作台，而不是会执行任意命令的聊天机器人。
        </p>
      </section>

      <section className="principles" aria-label="Architecture principles">
        {principles.map(([title, description]) => (
          <article key={title}>
            <span aria-hidden="true" />
            <h2>{title}</h2>
            <p>{description}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
