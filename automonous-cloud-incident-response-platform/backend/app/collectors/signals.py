"""Collectors that gather signals from Prometheus, service logs, and K8s-like status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings


async def fetch_prometheus_metrics(service: str = "payments-api") -> dict[str, Any]:
    queries = {
        "error_rate": f'rate(http_requests_total{{service="{service}",status=~"5.."}}[1m])',
        "request_rate": f'rate(http_requests_total{{service="{service}"}}[1m])',
        "latency_p99": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[1m])) by (le))',
        "memory_bytes": f'app_resident_memory_bytes{{service="{service}"}}',
        "up": f'app_up{{service="{service}"}}',
    }
    results: dict[str, Any] = {"queried_at": datetime.now(timezone.utc).isoformat(), "values": {}}

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, query in queries.items():
            try:
                resp = await client.get(
                    f"{settings.prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                data = resp.json()
                series = data.get("data", {}).get("result", [])
                if series:
                    results["values"][name] = float(series[0]["value"][1])
                else:
                    results["values"][name] = None
            except Exception as exc:  # noqa: BLE001
                results["values"][name] = None
                results.setdefault("errors", {})[name] = str(exc)

    return results


async def fetch_service_logs(limit: int = 40) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{settings.chaos_target_url}/logs", params={"limit": limit})
            resp.raise_for_status()
            return resp.json().get("logs", [])
        except Exception as exc:  # noqa: BLE001
            return [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "ERROR",
                    "message": f"Unable to fetch logs from chaos target: {exc}",
                }
            ]


async def fetch_k8s_status() -> dict[str, Any]:
    """Simulated Kubernetes pod/deployment status from the chaos target."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{settings.chaos_target_url}/k8s/status")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            return {
                "cluster": "demo-cluster",
                "namespace": "payments",
                "deployment": "payments-api",
                "ready_replicas": 0,
                "desired_replicas": 3,
                "pods": [],
                "error": str(exc),
            }


async def fetch_chaos_health() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{settings.chaos_target_url}/health")
            return {"ok": resp.status_code == 200, "body": resp.json(), "status_code": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "status_code": 0}


async def inject_scenario(scenario: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.chaos_target_url}/chaos/{scenario}")
        resp.raise_for_status()
        return resp.json()


async def remediate_on_target(action: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{settings.chaos_target_url}/remediate", json={"action": action})
        resp.raise_for_status()
        return resp.json()
