import { useCallback, useEffect, useState } from "react";
import { api, Incident, Memory, Stats } from "./api/client";

const SCENARIOS = [
  { id: "oom", title: "OOM Kill", desc: "Memory limit exceeded → pod OOMKilled" },
  { id: "crash_loop", title: "CrashLoopBackOff", desc: "Bad config causes restart thrash" },
  { id: "high_latency", title: "High Latency", desc: "p99 SLO breach / worker saturation" },
  { id: "dependency_down", title: "Dependency Down", desc: "payments-db connection refused" },
] as const;

function badgeClass(value: string): string {
  return `badge ${value.toLowerCase()}`;
}

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [llmEnabled, setLlmEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, list, mem, health] = await Promise.all([
        api.stats(),
        api.incidents(),
        api.memory(),
        api.health(),
      ]);
      setStats(s);
      setIncidents(list);
      setMemories(mem);
      setLlmEnabled(health.llm_enabled);
      setError(null);
      if (!selectedId && list[0]) {
        setSelectedId(list[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  }, [selectedId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 4000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    void api.incident(selectedId).then(setSelected).catch(() => setSelected(null));
  }, [selectedId, incidents]);

  async function runScenario(scenario: string) {
    setBusy(true);
    setError(null);
    try {
      const incident = await api.simulate(scenario);
      setSelectedId(incident.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">ACIRP</div>
          <h1>Autonomous Cloud Incident Response</h1>
          <p>
            Detect → diagnose → remediate → postmortem → learn. LangGraph agent over Prometheus,
            Kubernetes signals, and Kafka alerts.
          </p>
        </div>
        <div className="pill-row">
          <span className={`pill ${llmEnabled ? "on" : ""}`}>
            LLM {llmEnabled ? "OpenAI" : "heuristic mode"}
          </span>
          <span className="pill on">Kafka / Redpanda</span>
          <span className="pill on">Prometheus</span>
          <span className="pill on">Postgres memory</span>
        </div>
      </header>

      <section className="stats">
        <div className="stat">
          <div className="label">Total incidents</div>
          <div className="value">{stats?.total_incidents ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Open</div>
          <div className="value">{stats?.open_incidents ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Resolved</div>
          <div className="value">{stats?.resolved_incidents ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="label">Learned patterns</div>
          <div className="value">{stats?.memories ?? "—"}</div>
        </div>
      </section>

      <div className="layout">
        <aside className="panel">
          <h2>Inject failure</h2>
          <div className="scenario-grid">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                className="scenario-btn"
                disabled={busy}
                onClick={() => void runScenario(s.id)}
              >
                <strong>{s.title}</strong>
                <span>{s.desc}</span>
              </button>
            ))}
          </div>
          {error && <p className="error">{error}</p>}

          <h2>Incidents</h2>
          <div className="incident-list">
            {incidents.length === 0 && <div className="empty">No incidents yet — inject a failure.</div>}
            {incidents.map((inc) => (
              <button
                key={inc.id}
                className={`incident-item ${selectedId === inc.id ? "active" : ""}`}
                onClick={() => setSelectedId(inc.id)}
              >
                <div className="row">
                  <div className="title">{inc.title}</div>
                  <span className={badgeClass(inc.status)}>{inc.status}</span>
                </div>
                <div className="meta">
                  {inc.service} · {inc.severity} · {(inc.confidence * 100).toFixed(0)}% conf
                </div>
              </button>
            ))}
          </div>

          <h2 style={{ marginTop: 20 }}>Incident memory</h2>
          <div className="memory-list">
            {memories.length === 0 && <div className="empty">Patterns appear after remediations.</div>}
            {memories.map((m) => (
              <div key={m.id} className="memory-item">
                <div className="times">seen ×{m.times_seen}</div>
                <strong>{m.symptoms_summary}</strong>
                <div className="meta">{m.root_cause.slice(0, 120)}…</div>
              </div>
            ))}
          </div>
        </aside>

        <main className="panel">
          <h2>Agent workspace</h2>
          {!selected && <div className="empty">Select or simulate an incident to inspect the agent trace.</div>}
          {selected && (
            <div className="detail-grid">
              <div>
                <div className="row" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span className={badgeClass(selected.status)}>{selected.status}</span>
                  <span className={badgeClass(selected.severity)}>{selected.severity}</span>
                  {selected.auto_remediated && <span className="badge ok">auto-remediated</span>}
                </div>
                <h3 style={{ margin: "12px 0 6px", fontSize: "1.15rem" }}>{selected.title}</h3>
                <div className="meta">
                  {selected.service} @ {selected.cluster} · confidence {(selected.confidence * 100).toFixed(0)}%
                </div>
              </div>

              <div className="detail-block">
                <h3>Root cause</h3>
                <p>{selected.root_cause || "Pending diagnosis…"}</p>
              </div>

              <div className="detail-block">
                <h3>Remediation plan</h3>
                <p>{selected.remediation_plan || "—"}</p>
              </div>

              <div className="detail-block">
                <h3>Agent trace</h3>
                <div className="trace">
                  {selected.agent_trace.map((t, i) => (
                    <div className="trace-step" key={`${t.step}-${i}`}>
                      <div className="step">{t.step}</div>
                      <div className="detail">{t.detail}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="detail-block">
                <h3>Actions</h3>
                <pre>{JSON.stringify(selected.remediation_actions, null, 2)}</pre>
              </div>

              <div className="detail-block">
                <h3>Postmortem</h3>
                <pre>{selected.postmortem || "—"}</pre>
              </div>

              <div className="detail-block">
                <h3>Logs snapshot</h3>
                <pre>{JSON.stringify(selected.logs_snapshot?.slice?.(0, 12) ?? selected.logs_snapshot, null, 2)}</pre>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
