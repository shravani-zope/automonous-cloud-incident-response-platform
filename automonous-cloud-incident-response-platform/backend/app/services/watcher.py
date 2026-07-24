"""Background watcher that polls chaos-target / Prometheus and opens incidents."""

from __future__ import annotations

import asyncio
import logging

from app.collectors import signals
from app.config import settings
from app.db.session import SessionLocal
from app.services import incidents as incident_service
from app.services.events import event_bus

logger = logging.getLogger(__name__)

SCENARIO_TITLES = {
    "oom": "OOMKilled: payments-api memory limit exceeded",
    "crash_loop": "CrashLoopBackOff: payments-api restart thrashing",
    "high_latency": "SLO breach: payments-api p99 latency elevated",
    "dependency_down": "Dependency failure: payments-db unreachable",
}


class IncidentWatcher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Incident watcher started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("Watcher tick failed")
            await asyncio.sleep(settings.poll_interval_seconds)

    async def _tick(self) -> None:
        health = await signals.fetch_chaos_health()
        body = health.get("body") or {}
        if health.get("ok") and not body.get("degraded"):
            return

        scenario = body.get("active_chaos") or "unknown"
        fingerprint = f"payments-api:{scenario}"
        title = SCENARIO_TITLES.get(scenario, f"Service degraded: payments-api ({scenario})")

        async with SessionLocal() as session:
            incident = await incident_service.create_and_run_incident(
                session,
                title=title,
                service="payments-api",
                cluster="demo-cluster",
                severity="critical" if scenario in {"oom", "crash_loop", "dependency_down"} else "high",
                alert_fingerprint=fingerprint,
                symptoms={
                    "scenario": scenario,
                    "health": body,
                    "source": "watcher",
                },
            )
            await event_bus.publish_update(
                {"incident_id": incident.id, "status": incident.status, "title": incident.title}
            )


watcher = IncidentWatcher()
