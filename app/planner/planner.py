"""plan_turn(): one Jev request -> Plan (or an escalation to the legacy planner)."""
from __future__ import annotations

from typing import Optional

from agents.deps import FarmerContext
from helpers.utils import get_logger
from app.planner import jev
from app.planner.config import PlannerSettings
from app.planner.decode import decode
from app.planner.models import Plan
from app.planner.questions import TurnGates, build_questions, build_state, gates_for

logger = get_logger(__name__)


async def plan_turn(
    deps: FarmerContext,
    history_pairs: list[tuple[str, str]],
    settings: PlannerSettings,
    *,
    original_query: Optional[str] = None,
    gates: Optional[TurnGates] = None,
) -> Plan:
    gates = gates or gates_for(deps, settings)
    state = build_state(deps, gates, history_pairs[-settings.history_pairs:], original_query=original_query)
    questions = build_questions(deps, gates)
    try:
        result = await jev.evaluate(state, questions, model=settings.typesafe_model, timeout_s=settings.typesafe_timeout_s)
    except jev.JevUnavailable as exc:
        plan = Plan(intent="unknown", tool_calls=[], escalate=True, escalate_reason=f"jev unavailable: {exc}",
                    confidence=0.0, questions=questions, state=state)
        return plan
    plan = decode(deps, gates, result.answers, settings)
    plan.questions = questions
    plan.state = state
    plan.jev_ms = result.ms
    plan.jev_input_tokens = result.input_tokens
    plan.jev_model = result.model
    plan.jev_request_id = result.request_id
    logger.info(
        "jev plan intent=%s tools=%s escalate=%s conf=%.2f ms=%.0f tokens=%s",
        plan.intent, [(c.name, c.args) for c in plan.tool_calls], plan.escalate, plan.confidence, plan.ms if hasattr(plan, 'ms') else plan.jev_ms, result.input_tokens,
    )
    return plan
