export type Incident = {
  id: string;
  title: string;
  status: string;
  severity: string;
  service: string;
  cluster: string;
  alert_fingerprint: string;
  symptoms: Record<string, unknown>;
  metrics_snapshot: Record<string, unknown>;
  logs_snapshot: Array<Record<string, unknown>>;
  root_cause: string | null;
  remediation_plan: string | null;
  remediation_actions: Array<Record<string, unknown>>;
  postmortem: string | null;
  confidence: number;
  auto_remediated: boolean;
  agent_trace: Array<{ step: string; detail: string; at: string; data?: Record<string, unknown> }>;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type Stats = {
  total_incidents: number;
  open_incidents: number;
  resolved_incidents: number;
  memories: number;
  avg_confidence: number;
};

export type Memory = {
  id: string;
  fingerprint: string;
  service: string;
  symptoms_summary: string;
  root_cause: string;
  remediation: string;
  success: boolean;
  times_seen: number;
  created_at: string;
  updated_at: string;
};

const API = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; llm_enabled: boolean; auto_remediate: boolean }>("/health"),
  stats: () => request<Stats>("/stats"),
  incidents: () => request<Incident[]>("/incidents"),
  incident: (id: string) => request<Incident>(`/incidents/${id}`),
  memory: () => request<Memory[]>("/memory"),
  simulate: (scenario: string) =>
    request<Incident>("/simulate", { method: "POST", body: JSON.stringify({ scenario }) }),
  liveSignals: () => request<Record<string, unknown>>("/signals/live"),
};
