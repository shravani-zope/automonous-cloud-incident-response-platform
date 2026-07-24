"""LangGraph-style autonomous incident response agent.

Pipeline:
  detect → collect_signals → recall_memory → diagnose → plan_remediation → execute → postmortem → learn
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from app.collectors import signals
from app.config import settings


class AgentState(TypedDict, total=False):
    incident_id: str
    title: str
    service: str
    severity: str
    symptoms: dict[str, Any]
    metrics: dict[str, Any]
    logs: list[dict[str, Any]]
    k8s: dict[str, Any]
    memories: list[dict[str, Any]]
    root_cause: str
    remediation_plan: str
    remediation_actions: list[dict[str, Any]]
    postmortem: str
    confidence: float
    auto_remediate: bool
    status: str
    trace: list[dict[str, Any]]
    error: str


def _trace(state: AgentState, step: str, detail: str, data: Optional[dict] = None) -> list[dict[str, Any]]:
    entry = {
        "step": step,
        "detail": detail,
        "at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    return [*state.get("trace", []), entry]


def _heuristic_diagnose(state: AgentState) -> tuple[str, str, float, list[dict[str, Any]]]:
    """Rule-based diagnosis used when OpenAI is unavailable."""
    symptoms = state.get("symptoms", {})
    scenario = str(symptoms.get("scenario", "")).lower()
    k8s = state.get("k8s", {})
    pods = k8s.get("pods", [])
    restarts = sum(int(p.get("restarts", 0)) for p in pods)
    oom = any(p.get("last_state") == "OOMKilled" or "oom" in str(p.get("reason", "")).lower() for p in pods)
    logs = " ".join(str(l.get("message", "")) for l in state.get("logs", [])[:15]).lower()

    playbooks = {
        "oom": (
            "Pod OOMKilled: payments-api exceeded its memory limit (likely memory leak or undersized limit).",
            "Increase memory limit, restart deployment, and enable memory profiling.",
            0.91,
            [
                {"action": "increase_memory_limit", "params": {"memory": "512Mi"}},
                {"action": "restart_deployment", "params": {}},
            ],
        ),
        "crash_loop": (
            "CrashLoopBackOff: container exits on startup due to bad config or failing readiness probe.",
            "Rollback to last known good image/config and restart the deployment.",
            0.88,
            [
                {"action": "rollback_deployment", "params": {}},
                {"action": "restart_deployment", "params": {}},
            ],
        ),
        "high_latency": (
            "Elevated p99 latency from saturating worker pool / slow downstream calls.",
            "Scale replicas and clear connection pool saturation; reset latency injection.",
            0.84,
            [
                {"action": "scale_replicas", "params": {"replicas": 5}},
                {"action": "clear_latency_injection", "params": {}},
            ],
        ),
        "dependency_down": (
            "Downstream dependency unavailable (connection refused to payments-db / redis).",
            "Failover to healthy dependency endpoint and restart affected pods.",
            0.86,
            [
                {"action": "failover_dependency", "params": {}},
                {"action": "restart_deployment", "params": {}},
            ],
        ),
    }

    if scenario in playbooks:
        return playbooks[scenario]

    if oom:
        return playbooks["oom"]
    if restarts >= 3 or "crashloopbackoff" in logs:
        return playbooks["crash_loop"]
    if "econnrefused" in logs or "connection refused" in logs:
        return playbooks["dependency_down"]
    if "latency" in logs or "timeout" in logs:
        return playbooks["high_latency"]

    return (
        "Degraded service health with elevated errors; unable to pinpoint a single root cause from signals alone.",
        "Restart deployment and re-check Prometheus SLOs.",
        0.55,
        [{"action": "restart_deployment", "params": {}}],
    )


async def _llm_diagnose(state: AgentState) -> tuple[str, str, float, list[dict[str, Any]]]:
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=settings.openai_model, api_key=settings.openai_api_key, temperature=0.1)
    payload = {
        "title": state.get("title"),
        "service": state.get("service"),
        "symptoms": state.get("symptoms"),
        "metrics": state.get("metrics"),
        "logs": state.get("logs", [])[-25:],
        "k8s": state.get("k8s"),
        "similar_past_incidents": state.get("memories", []),
    }
    system = SystemMessage(
        content=(
            "You are an SRE incident response agent for Kubernetes/cloud services. "
            "Diagnose root cause and propose safe remediation actions. "
            "Respond ONLY with JSON keys: root_cause, remediation_plan, confidence (0-1), "
            "actions (array of {action, params}). "
            "Allowed actions: increase_memory_limit, restart_deployment, rollback_deployment, "
            "scale_replicas, clear_latency_injection, failover_dependency, heal."
        )
    )
    human = HumanMessage(content=json.dumps(payload, default=str))
    resp = await llm.ainvoke([system, human])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return _heuristic_diagnose(state)
    data = json.loads(match.group(0))
    return (
        str(data.get("root_cause", "Unknown")),
        str(data.get("remediation_plan", "Investigate further")),
        float(data.get("confidence", 0.7)),
        list(data.get("actions", [{"action": "heal", "params": {}}])),
    )


async def collect_signals_node(state: AgentState) -> AgentState:
    service = state.get("service", "payments-api")
    metrics = await signals.fetch_prometheus_metrics(service)
    logs = await signals.fetch_service_logs()
    k8s = await signals.fetch_k8s_status()
    return {
        **state,
        "metrics": metrics,
        "logs": logs,
        "k8s": k8s,
        "status": "investigating",
        "trace": _trace(
            state,
            "collect_signals",
            "Gathered Prometheus metrics, service logs, and Kubernetes status",
            {"metrics": metrics.get("values"), "log_count": len(logs), "pods": len(k8s.get("pods", []))},
        ),
    }


async def recall_memory_node(state: AgentState) -> AgentState:
    memories = state.get("memories", [])
    detail = f"Recalled {len(memories)} similar past incident(s)" if memories else "No similar past incidents"
    return {
        **state,
        "trace": _trace(state, "recall_memory", detail, {"count": len(memories)}),
    }


async def diagnose_node(state: AgentState) -> AgentState:
    if settings.use_llm:
        try:
            root_cause, plan, confidence, actions = await _llm_diagnose(state)
            mode = "llm"
        except Exception as exc:  # noqa: BLE001
            root_cause, plan, confidence, actions = _heuristic_diagnose(state)
            mode = f"heuristic_fallback ({exc})"
    else:
        root_cause, plan, confidence, actions = _heuristic_diagnose(state)
        mode = "heuristic"

    # Boost confidence if memory matches
    if state.get("memories"):
        confidence = min(0.98, confidence + 0.05)
        plan = f"{plan}\n\n(Informed by {len(state['memories'])} prior incident(s).)"

    return {
        **state,
        "root_cause": root_cause,
        "remediation_plan": plan,
        "remediation_actions": actions,
        "confidence": confidence,
        "status": "root_cause_found",
        "trace": _trace(
            state,
            "diagnose",
            f"Root cause identified via {mode}",
            {"root_cause": root_cause, "confidence": confidence, "actions": actions},
        ),
    }


async def plan_remediation_node(state: AgentState) -> AgentState:
    return {
        **state,
        "status": "remediating",
        "trace": _trace(
            state,
            "plan_remediation",
            state.get("remediation_plan", "No plan"),
            {"actions": state.get("remediation_actions", [])},
        ),
    }


async def execute_remediation_node(state: AgentState) -> AgentState:
    if not state.get("auto_remediate", settings.auto_remediate):
        return {
            **state,
            "status": "root_cause_found",
            "trace": _trace(state, "execute_remediation", "Auto-remediation disabled; plan ready for human approval"),
        }

    results = []
    actions = state.get("remediation_actions", [])
    # Always end with heal to clear chaos state in demo
    action_names = [a.get("action", "heal") for a in actions]
    if "heal" not in action_names:
        actions = [*actions, {"action": "heal", "params": {}}]

    for action in actions:
        name = action.get("action", "heal")
        try:
            result = await signals.remediate_on_target(name)
            results.append({"action": name, "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            results.append({"action": name, "ok": False, "error": str(exc)})

    success = any(r.get("ok") for r in results)
    return {
        **state,
        "remediation_actions": results,
        "status": "resolved" if success else "failed",
        "trace": _trace(state, "execute_remediation", "Executed remediation actions", {"results": results}),
    }


async def postmortem_node(state: AgentState) -> AgentState:
    timeline = "\n".join(f"- [{t['at']}] {t['step']}: {t['detail']}" for t in state.get("trace", []))
    postmortem = f"""# Incident Postmortem

## Summary
{state.get('title', 'Untitled incident')} on service `{state.get('service')}` ({state.get('severity')} severity).

## Root Cause
{state.get('root_cause', 'N/A')}

## Impact
Service degradation detected via Prometheus / Kubernetes signals. Confidence: {state.get('confidence', 0):.0%}.

## Resolution
{state.get('remediation_plan', 'N/A')}

### Actions Taken
{json.dumps(state.get('remediation_actions', []), indent=2)}

## Timeline
{timeline}

## Follow-ups
- Add alert on early symptom signals
- Validate resource limits and dependency SLOs
- Capture runbook update from this incident memory
"""
    return {
        **state,
        "postmortem": postmortem,
        "status": state.get("status", "resolved"),
        "trace": _trace(state, "write_postmortem", "Generated incident postmortem"),
    }


async def learn_node(state: AgentState) -> AgentState:
    return {
        **state,
        "trace": _trace(
            state,
            "learn",
            "Persisting pattern to incident memory for future recall",
            {
                "root_cause": state.get("root_cause"),
                "remediation": state.get("remediation_plan"),
            },
        ),
    }


def should_execute(state: AgentState) -> Literal["execute", "skip"]:
    if state.get("auto_remediate", settings.auto_remediate):
        return "execute"
    return "skip"


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("collect_signals", collect_signals_node)
    graph.add_node("recall_memory", recall_memory_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("plan_remediation", plan_remediation_node)
    graph.add_node("execute_remediation", execute_remediation_node)
    graph.add_node("write_postmortem", postmortem_node)
    graph.add_node("learn", learn_node)

    graph.set_entry_point("collect_signals")
    graph.add_edge("collect_signals", "recall_memory")
    graph.add_edge("recall_memory", "diagnose")
    graph.add_edge("diagnose", "plan_remediation")
    graph.add_conditional_edges(
        "plan_remediation",
        should_execute,
        {"execute": "execute_remediation", "skip": "write_postmortem"},
    )
    graph.add_edge("execute_remediation", "write_postmortem")
    graph.add_edge("write_postmortem", "learn")
    graph.add_edge("learn", END)
    return graph.compile()


incident_agent = build_agent()
