from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, Enum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    ROOT_CAUSE_FOUND = "root_cause_found"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    FAILED = "failed"


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(64), default=IncidentStatus.DETECTED.value)
    severity: Mapped[str] = mapped_column(String(32), default=IncidentSeverity.MEDIUM.value)
    service: Mapped[str] = mapped_column(String(128), default="unknown")
    cluster: Mapped[str] = mapped_column(String(128), default="demo-cluster")
    alert_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    symptoms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    logs_snapshot: Mapped[list[Any]] = mapped_column(JSON, default=list)
    root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remediation_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remediation_actions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    postmortem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    auto_remediated: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_trace: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class IncidentMemory(Base):
    """Learned patterns from past incidents for future root-cause matching."""

    __tablename__ = "incident_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    service: Mapped[str] = mapped_column(String(128))
    symptoms_summary: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    remediation: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    times_seen: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
