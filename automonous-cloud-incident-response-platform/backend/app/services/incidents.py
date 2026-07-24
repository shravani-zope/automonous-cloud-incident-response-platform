from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import incident_agent
from app.config import settings
from app.db.models import Incident, IncidentMemory, IncidentStatus


async def list_incidents(session: AsyncSession, limit: int = 50) -> list[Incident]:
    result = await session.execute(select(Incident).order_by(Incident.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def get_incident(session: AsyncSession, incident_id: str) -> Optional[Incident]:
    return await session.get(Incident, incident_id)


async def list_memories(session: AsyncSession, limit: int = 50) -> list[IncidentMemory]:
    result = await session.execute(
        select(IncidentMemory).order_by(IncidentMemory.updated_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    total = await session.scalar(select(func.count()).select_from(Incident)) or 0
    resolved = (
        await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.RESOLVED.value)
        )
        or 0
    )
    open_count = total - resolved
    memories = await session.scalar(select(func.count()).select_from(IncidentMemory)) or 0
    avg_conf = await session.scalar(select(func.avg(Incident.confidence))) or 0.0
    return {
        "total_incidents": total,
        "open_incidents": open_count,
        "resolved_incidents": resolved,
        "memories": memories,
        "avg_confidence": float(avg_conf),
    }


async def find_similar_memories(
    session: AsyncSession, service: str, fingerprint: str, limit: int = 5
) -> list[IncidentMemory]:
    # Prefer exact fingerprint, then same service
    by_fp = await session.execute(
        select(IncidentMemory)
        .where(IncidentMemory.fingerprint == fingerprint)
        .order_by(IncidentMemory.times_seen.desc())
        .limit(limit)
    )
    memories = list(by_fp.scalars().all())
    if memories:
        return memories
    by_svc = await session.execute(
        select(IncidentMemory)
        .where(IncidentMemory.service == service)
        .order_by(IncidentMemory.times_seen.desc())
        .limit(limit)
    )
    return list(by_svc.scalars().all())


async def upsert_memory(
    session: AsyncSession,
    *,
    fingerprint: str,
    service: str,
    symptoms_summary: str,
    root_cause: str,
    remediation: str,
    success: bool,
) -> IncidentMemory:
    existing = await session.execute(
        select(IncidentMemory).where(
            IncidentMemory.fingerprint == fingerprint,
            IncidentMemory.service == service,
        )
    )
    mem = existing.scalar_one_or_none()
    if mem:
        mem.times_seen += 1
        mem.root_cause = root_cause
        mem.remediation = remediation
        mem.symptoms_summary = symptoms_summary
        mem.success = success
        mem.updated_at = datetime.now(timezone.utc)
    else:
        mem = IncidentMemory(
            fingerprint=fingerprint,
            service=service,
            symptoms_summary=symptoms_summary,
            root_cause=root_cause,
            remediation=remediation,
            success=success,
        )
        session.add(mem)
    await session.commit()
    await session.refresh(mem)
    return mem


async def create_and_run_incident(
    session: AsyncSession,
    *,
    title: str,
    service: str,
    cluster: str,
    severity: str,
    alert_fingerprint: str,
    symptoms: dict[str, Any],
    auto_remediate: Optional[bool] = None,
) -> Incident:
    # Deduplicate open incidents with same fingerprint
    existing = await session.execute(
        select(Incident).where(
            Incident.alert_fingerprint == alert_fingerprint,
            Incident.status.notin_(
                [IncidentStatus.RESOLVED.value, IncidentStatus.FAILED.value]
            ),
        )
    )
    open_incident = existing.scalar_one_or_none()
    if open_incident:
        return open_incident

    incident = Incident(
        title=title,
        service=service,
        cluster=cluster,
        severity=severity,
        alert_fingerprint=alert_fingerprint,
        symptoms=symptoms,
        status=IncidentStatus.DETECTED.value,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    memories = await find_similar_memories(session, service, alert_fingerprint)
    memory_payload = [
        {
            "root_cause": m.root_cause,
            "remediation": m.remediation,
            "times_seen": m.times_seen,
            "symptoms_summary": m.symptoms_summary,
        }
        for m in memories
    ]

    initial_state = {
        "incident_id": incident.id,
        "title": title,
        "service": service,
        "severity": severity,
        "symptoms": symptoms,
        "memories": memory_payload,
        "auto_remediate": settings.auto_remediate if auto_remediate is None else auto_remediate,
        "trace": [
            {
                "step": "detect",
                "detail": f"Incident detected: {title}",
                "at": datetime.now(timezone.utc).isoformat(),
                "data": symptoms,
            }
        ],
        "status": "detected",
    }

    final_state = await incident_agent.ainvoke(initial_state)

    incident.metrics_snapshot = final_state.get("metrics", {})
    incident.logs_snapshot = final_state.get("logs", [])
    incident.root_cause = final_state.get("root_cause")
    incident.remediation_plan = final_state.get("remediation_plan")
    incident.remediation_actions = final_state.get("remediation_actions", [])
    incident.postmortem = final_state.get("postmortem")
    incident.confidence = float(final_state.get("confidence") or 0)
    incident.agent_trace = final_state.get("trace", [])
    incident.status = final_state.get("status", IncidentStatus.INVESTIGATING.value)
    incident.auto_remediated = bool(final_state.get("auto_remediate", settings.auto_remediate)) and incident.status == IncidentStatus.RESOLVED.value
    if incident.status == IncidentStatus.RESOLVED.value:
        incident.resolved_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(incident)

    if incident.root_cause and incident.remediation_plan:
        await upsert_memory(
            session,
            fingerprint=alert_fingerprint,
            service=service,
            symptoms_summary=title,
            root_cause=incident.root_cause,
            remediation=incident.remediation_plan,
            success=incident.status == IncidentStatus.RESOLVED.value,
        )

    return incident
