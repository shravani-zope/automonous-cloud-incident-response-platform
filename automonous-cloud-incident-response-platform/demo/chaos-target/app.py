"""Demo payments-api that can be driven into failure scenarios and remediated."""

from __future__ import annotations

import random
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, Query, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

app = FastAPI(title="payments-api (chaos target)")

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["service", "status", "endpoint"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency",
    ["service", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
MEMORY_GAUGE = Gauge("app_resident_memory_bytes", "Simulated RSS", ["service"])
UP = Gauge("app_up", "Target up", ["service", "job"])

SERVICE = "payments-api"
_lock = Lock()
_logs: deque[dict[str, Any]] = deque(maxlen=200)


def _healthy_pods() -> list[dict[str, Any]]:
    return [
        {"name": f"payments-api-{i}", "phase": "Running", "restarts": 0, "last_state": "Running", "reason": ""}
        for i in range(3)
    ]


_state: dict[str, Any] = {
    "degraded": False,
    "active_chaos": None,
    "error_rate": 0.02,
    "latency_ms": 40,
    "memory_bytes": 120_000_000,
    "ready_replicas": 3,
    "desired_replicas": 3,
    "pods": _healthy_pods(),
    "dependency_ok": True,
}


def _log(level: str, message: str) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "service": SERVICE,
    }
    with _lock:
        _logs.appendleft(entry)


def _apply_metrics() -> None:
    MEMORY_GAUGE.labels(service=SERVICE).set(_state["memory_bytes"])
    UP.labels(service=SERVICE, job="chaos-target").set(0 if _state["degraded"] and _state["active_chaos"] == "crash_loop" else 1)


def _healthy_pods_refresh() -> None:
    _state["pods"] = _healthy_pods()
    _state["ready_replicas"] = 3
    _state["desired_replicas"] = 3
    _state["memory_bytes"] = 120_000_000
    _state["error_rate"] = 0.02
    _state["latency_ms"] = 40
    _state["dependency_ok"] = True
    _state["degraded"] = False
    _state["active_chaos"] = None


@app.on_event("startup")
async def startup() -> None:
    _healthy_pods_refresh()
    _apply_metrics()
    _log("INFO", "payments-api started — ready to serve traffic")


class RemediateBody(BaseModel):
    action: str
    params: dict[str, Any] | None = None


@app.get("/health")
async def health() -> dict[str, Any]:
    start = time.perf_counter()
    status = "200"
    try:
        if _state["active_chaos"] == "crash_loop":
            status = "503"
            REQUESTS.labels(service=SERVICE, status=status, endpoint="health").inc()
            LATENCY.labels(service=SERVICE, endpoint="health").observe(time.perf_counter() - start)
            return {
                "status": "unhealthy",
                "degraded": True,
                "active_chaos": _state["active_chaos"],
                "service": SERVICE,
            }
        if _state["degraded"]:
            status = "200"
            body = {
                "status": "degraded",
                "degraded": True,
                "active_chaos": _state["active_chaos"],
                "service": SERVICE,
                "error_rate": _state["error_rate"],
                "latency_ms": _state["latency_ms"],
            }
        else:
            body = {
                "status": "healthy",
                "degraded": False,
                "active_chaos": None,
                "service": SERVICE,
            }
        REQUESTS.labels(service=SERVICE, status=status, endpoint="health").inc()
        LATENCY.labels(service=SERVICE, endpoint="health").observe(time.perf_counter() - start)
        return body
    finally:
        _apply_metrics()


@app.get("/pay")
async def pay() -> dict[str, Any]:
    start = time.perf_counter()
    # Simulate latency injection
    delay = _state["latency_ms"] / 1000.0
    if delay > 0:
        time.sleep(min(delay, 2.0))

    fail = False
    if not _state["dependency_ok"]:
        fail = True
        _log("ERROR", "ECONNREFUSED payments-db:5432 — connection refused")
    elif random.random() < _state["error_rate"]:
        fail = True
        _log("ERROR", "payment processing failed — upstream timeout")

    if _state["active_chaos"] == "oom":
        _log("ERROR", "java.lang.OutOfMemoryError: Java heap space / OOMKilled")

    status = "500" if fail else "200"
    REQUESTS.labels(service=SERVICE, status=status, endpoint="pay").inc()
    LATENCY.labels(service=SERVICE, endpoint="pay").observe(time.perf_counter() - start)
    _apply_metrics()

    if fail:
        return {"ok": False, "error": "payment_failed", "chaos": _state["active_chaos"]}
    return {"ok": True, "amount": 42.0, "currency": "USD"}


@app.get("/logs")
async def logs(limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    with _lock:
        items = list(_logs)[:limit]
    return {"logs": items}


@app.get("/k8s/status")
async def k8s_status() -> dict[str, Any]:
    return {
        "cluster": "demo-cluster",
        "namespace": "payments",
        "deployment": "payments-api",
        "ready_replicas": _state["ready_replicas"],
        "desired_replicas": _state["desired_replicas"],
        "pods": _state["pods"],
        "memory_limit": "256Mi" if _state["active_chaos"] == "oom" else "512Mi",
    }


@app.post("/chaos/{scenario}")
async def inject_chaos(scenario: str) -> dict[str, Any]:
    if scenario == "heal":
        return await remediate(RemediateBody(action="heal"))

    with _lock:
        _logs.clear()

    _state["degraded"] = True
    _state["active_chaos"] = scenario

    if scenario == "oom":
        _state["memory_bytes"] = 510_000_000
        _state["error_rate"] = 0.45
        _state["ready_replicas"] = 1
        _state["pods"] = [
            {"name": "payments-api-0", "phase": "Running", "restarts": 2, "last_state": "OOMKilled", "reason": "OOMKilled"},
            {"name": "payments-api-1", "phase": "CrashLoopBackOff", "restarts": 5, "last_state": "OOMKilled", "reason": "OOMKilled"},
            {"name": "payments-api-2", "phase": "Pending", "restarts": 0, "last_state": "OOMKilled", "reason": "OOMKilled"},
        ]
        _log("ERROR", "Pod payments-api-1 OOMKilled: memory limit 256Mi exceeded")
        _log("WARN", "Back-off restarting failed container")
    elif scenario == "crash_loop":
        _state["error_rate"] = 1.0
        _state["ready_replicas"] = 0
        _state["pods"] = [
            {
                "name": f"payments-api-{i}",
                "phase": "CrashLoopBackOff",
                "restarts": 8 + i,
                "last_state": "Error",
                "reason": "CrashLoopBackOff",
            }
            for i in range(3)
        ]
        _log("ERROR", "CrashLoopBackOff: container exit code 1 — invalid PAYMENTS_CONFIG")
        _log("ERROR", "Readiness probe failed: HTTP probe failed with statuscode: 503")
    elif scenario == "high_latency":
        _state["latency_ms"] = 1800
        _state["error_rate"] = 0.15
        _state["ready_replicas"] = 3
        _state["pods"] = _healthy_pods()
        _log("WARN", "p99 latency 1.8s exceeds SLO 250ms — worker pool saturated")
        _log("WARN", "downstream timeout calling ledger-service")
    elif scenario == "dependency_down":
        _state["dependency_ok"] = False
        _state["error_rate"] = 0.9
        _state["ready_replicas"] = 3
        _log("ERROR", "ECONNREFUSED payments-db:5432 — connection refused")
        _log("ERROR", "circuit breaker open for redis-cache")
    else:
        return {"ok": False, "error": f"unknown scenario: {scenario}"}

    _apply_metrics()
    _log("WARN", f"Chaos scenario activated: {scenario}")
    return {"ok": True, "scenario": scenario, "state": {k: v for k, v in _state.items() if k != "pods"}, "pods": _state["pods"]}


@app.post("/remediate")
async def remediate(body: RemediateBody) -> dict[str, Any]:
    action = body.action
    params = body.params or {}
    _log("INFO", f"Remediation action received: {action} params={params}")

    if action == "increase_memory_limit":
        _state["memory_bytes"] = 180_000_000
        _log("INFO", f"Memory limit increased to {params.get('memory', '512Mi')}")
    elif action == "restart_deployment":
        _log("INFO", "kubectl rollout restart deployment/payments-api")
        for pod in _state["pods"]:
            pod["phase"] = "Running"
            pod["last_state"] = "Running"
            pod["reason"] = ""
        _state["ready_replicas"] = _state["desired_replicas"]
    elif action == "rollback_deployment":
        _log("INFO", "kubectl rollout undo deployment/payments-api")
        _state["pods"] = _healthy_pods()
        _state["ready_replicas"] = 3
    elif action == "scale_replicas":
        replicas = int(params.get("replicas", 5))
        _state["desired_replicas"] = replicas
        _state["ready_replicas"] = replicas
        _log("INFO", f"Scaled payments-api to {replicas} replicas")
    elif action == "clear_latency_injection":
        _state["latency_ms"] = 40
        _log("INFO", "Cleared latency injection / reset worker pool")
    elif action == "failover_dependency":
        _state["dependency_ok"] = True
        _log("INFO", "Failed over to healthy payments-db replica")
    elif action == "heal":
        _healthy_pods_refresh()
        _log("INFO", "Full heal applied — service restored")
        _apply_metrics()
        return {"ok": True, "action": action, "healed": True}
    else:
        return {"ok": False, "error": f"unknown action: {action}"}

    # Partial recovery until heal
    if _state["active_chaos"] and action != "heal":
        _state["error_rate"] = max(0.05, _state["error_rate"] * 0.3)

    _apply_metrics()
    return {"ok": True, "action": action, "state_snapshot": {"degraded": _state["degraded"], "active_chaos": _state["active_chaos"]}}


@app.get("/metrics")
async def metrics() -> Response:
    # Generate a bit of traffic noise for Prometheus
    if not _state["degraded"]:
        REQUESTS.labels(service=SERVICE, status="200", endpoint="pay").inc()
        LATENCY.labels(service=SERVICE, endpoint="pay").observe(random.uniform(0.02, 0.08))
    _apply_metrics()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
