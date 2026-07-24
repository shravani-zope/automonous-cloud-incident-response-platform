from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class IncidentCreate(BaseModel):
    title: str
    service: str = "payments-api"
    cluster: str = "demo-cluster"
    severity: str = "high"
    alert_fingerprint: str
    symptoms: dict[str, Any] = Field(default_factory=dict)


class IncidentOut(BaseModel):
    id: str
    title: str
    status: str
    severity: str
    service: str
    cluster: str
    alert_fingerprint: str
    symptoms: dict[str, Any]
    metrics_snapshot: dict[str, Any]
    logs_snapshot: list[Any]
    root_cause: Optional[str]
    remediation_plan: Optional[str]
    remediation_actions: list[Any]
    postmortem: Optional[str]
    confidence: float
    auto_remediated: bool
    agent_trace: list[Any]
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}


class SimulateRequest(BaseModel):
    scenario: str = Field(
        default="oom",
        description="One of: oom, high_latency, crash_loop, dependency_down",
    )


class HealthOut(BaseModel):
    status: str
    llm_enabled: bool
    auto_remediate: bool


class MemoryOut(BaseModel):
    id: str
    fingerprint: str
    service: str
    symptoms_summary: str
    root_cause: str
    remediation: str
    success: bool
    times_seen: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DashboardStats(BaseModel):
    total_incidents: int
    open_incidents: int
    resolved_incidents: int
    memories: int
    avg_confidence: float
