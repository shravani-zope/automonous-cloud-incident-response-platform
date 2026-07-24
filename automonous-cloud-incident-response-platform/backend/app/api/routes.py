from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors import signals
from app.config import settings
from app.db.session import get_session
from app.models.schemas import (
    DashboardStats,
    HealthOut,
    IncidentOut,
    MemoryOut,
    SimulateRequest,
)
from app.services import incidents as incident_service
from app.services.events import event_bus
from app.services.watcher import SCENARIO_TITLES

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(
        status="ok",
        llm_enabled=settings.use_llm,
        auto_remediate=settings.auto_remediate,
    )


@router.get("/stats", response_model=DashboardStats)
async def stats(session: AsyncSession = Depends(get_session)) -> DashboardStats:
    data = await incident_service.dashboard_stats(session)
    return DashboardStats(**data)


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(session: AsyncSession = Depends(get_session)) -> list[IncidentOut]:
    rows = await incident_service.list_incidents(session)
    return [IncidentOut.model_validate(r) for r in rows]


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(incident_id: str, session: AsyncSession = Depends(get_session)) -> IncidentOut:
    row = await incident_service.get_incident(session, incident_id)
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentOut.model_validate(row)


@router.get("/memory", response_model=list[MemoryOut])
async def list_memory(session: AsyncSession = Depends(get_session)) -> list[MemoryOut]:
    rows = await incident_service.list_memories(session)
    return [MemoryOut.model_validate(r) for r in rows]


@router.post("/simulate", response_model=IncidentOut)
async def simulate(
    body: SimulateRequest,
    session: AsyncSession = Depends(get_session),
) -> IncidentOut:
    scenario = body.scenario
    if scenario not in SCENARIO_TITLES and scenario != "heal":
        raise HTTPException(
            status_code=400,
            detail="scenario must be one of: oom, high_latency, crash_loop, dependency_down",
        )

    await signals.inject_scenario(scenario)
    title = SCENARIO_TITLES.get(scenario, f"Simulated incident: {scenario}")
    fingerprint = f"payments-api:{scenario}:manual"

    await event_bus.publish_alert(
        {"type": "alert", "scenario": scenario, "service": "payments-api", "title": title}
    )

    incident = await incident_service.create_and_run_incident(
        session,
        title=title,
        service="payments-api",
        cluster="demo-cluster",
        severity="critical" if scenario in {"oom", "crash_loop", "dependency_down"} else "high",
        alert_fingerprint=fingerprint,
        symptoms={"scenario": scenario, "source": "manual_simulate"},
    )
    await event_bus.publish_update(
        {"incident_id": incident.id, "status": incident.status, "title": incident.title}
    )
    return IncidentOut.model_validate(incident)


@router.get("/signals/live")
async def live_signals() -> dict:
    metrics = await signals.fetch_prometheus_metrics()
    logs = await signals.fetch_service_logs(limit=20)
    k8s = await signals.fetch_k8s_status()
    health = await signals.fetch_chaos_health()
    return {"metrics": metrics, "logs": logs, "k8s": k8s, "health": health}
