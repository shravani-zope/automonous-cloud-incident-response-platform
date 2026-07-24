# Autonomous Cloud Incident Response Platform (ACIRP)

AI-driven SRE platform that detects cloud/Kubernetes failures, diagnoses root cause, remediates, writes a postmortem, and learns from past incidents.

## Architecture

```
┌─────────────┐   chaos    ┌──────────────┐  metrics  ┌────────────┐
│  Dashboard  │───────────▶│ chaos-target │──────────▶│ Prometheus │
│  (React)    │            │ payments-api │           └─────┬──────┘
└──────┬──────┘            └──────▲───────┘                 │
       │ API                      │ remediate               │ scrape
       ▼                          │                         ▼
┌─────────────┐  LangGraph  ┌─────┴────────┐         ┌────────────┐
│   Backend   │────────────▶│ collectors   │◀────────│  Grafana   │
│   FastAPI   │             │ logs/k8s/prom│         └────────────┘
└──────┬──────┘             └──────────────┘
       │
       ├── PostgreSQL (incidents + learned memory)
       └── Redpanda/Kafka (alert & update events)
```

### Agent pipeline (LangGraph)

1. **detect** — watcher or manual simulation
2. **collect_signals** — Prometheus metrics, service logs, K8s-like pod status
3. **recall_memory** — similar past incidents from Postgres
4. **diagnose** — OpenAI LLM if `OPENAI_API_KEY` is set, else heuristic SRE rules
5. **plan_remediation** — concrete actions
6. **execute** — call remediation APIs on the target service
7. **postmortem** — markdown report
8. **learn** — upsert pattern into incident memory

## Quick start

### Lite stack (recommended — runs immediately)

```bash
docker compose -f docker-compose.lite.yml up --build
```

Open **http://localhost:3000**, click a failure scenario, and watch the agent respond.

| Service    | URL |
|-----------|-----|
| Dashboard | http://localhost:3000 |
| API docs  | http://localhost:8000/docs |
| Chaos API | http://localhost:8081/health |

Optional LLM mode:

```bash
cp .env.example .env
# set OPENAI_API_KEY=sk-...
docker compose -f docker-compose.lite.yml up --build
```

### Full stack (Postgres + Redpanda/Kafka + Prometheus + Grafana)

```bash
docker compose up --build
```

| Extra service | URL |
|--------------|-----|
| Prometheus   | http://localhost:9090 |
| Grafana      | http://localhost:3001 (admin / admin) |

## Demo

1. Open the dashboard at http://localhost:3000
2. Click a failure scenario (OOM, CrashLoop, High Latency, or Dependency Down)
3. Watch the agent collect signals, diagnose, remediate, and write a postmortem
4. Re-run the same scenario — confidence rises as **incident memory** is reused

### API

```bash
curl -X POST http://localhost:8000/api/simulate \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"oom"}'
```

Scenarios: `oom` | `crash_loop` | `high_latency` | `dependency_down`

## Local development (without full compose)

```bash
# infra
docker compose up -d postgres redpanda prometheus chaos-target

# backend
cd backend && pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://acirp:acirp@localhost:5432/incidents
export KAFKA_BOOTSTRAP=localhost:19092
export PROMETHEUS_URL=http://localhost:9090
export CHAOS_TARGET_URL=http://localhost:8081
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev
```

## Tech stack

- **Kubernetes signals** (simulated pod/deployment status for the demo cluster)
- **Docker / Compose** for the full stack
- **Prometheus + Grafana** for metrics
- **Redpanda** (Kafka-compatible) for alert events
- **OpenAI + LangGraph** for the response agent
- **PostgreSQL** for incidents and learned memory
- **FastAPI + React** for the control plane UI

## Project layout

```
backend/          FastAPI + LangGraph agent
frontend/         React dashboard
demo/chaos-target Simulated payments-api with failure injection
prometheus/       Scrape config
grafana/          Datasource provisioning
```
