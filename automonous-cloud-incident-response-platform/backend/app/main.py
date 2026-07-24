import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import Response

from app.api.routes import router
from app.config import settings
from app.db.session import SessionLocal, init_db
from app.services import incidents as incident_service
from app.services.events import event_bus
from app.services.watcher import watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("acirp")

INCIDENTS_HANDLED = Counter("acirp_incidents_handled_total", "Incidents processed by the agent")


async def _on_alert(payload: dict) -> None:
    logger.info("Kafka alert received: %s", payload)
    INCIDENTS_HANDLED.inc()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await event_bus.start(_on_alert)
    await watcher.start()
    logger.info("ACIRP backend ready (llm=%s)", settings.use_llm)
    yield
    await watcher.stop()
    await event_bus.stop()


app = FastAPI(
    title="Autonomous Cloud Incident Response Platform",
    description="AI-driven detection, diagnosis, remediation, and learning for cloud incidents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root() -> dict:
    async with SessionLocal() as session:
        stats = await incident_service.dashboard_stats(session)
    return {
        "name": "Autonomous Cloud Incident Response Platform",
        "status": "running",
        "llm_enabled": settings.use_llm,
        "stats": stats,
    }
