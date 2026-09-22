"""Shadow mode: on a legacy turn, plan with Jev in the background (no tool
execution, no farmer-visible effect) and record tool-plan agreement."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from agents.deps import FarmerContext
from helpers.utils import get_logger
from app.planner import tracestore
from app.planner.arms import agreement, history_pairs_from_messages, legacy_tool_calls
from app.planner.config import PlannerSettings
from app.planner.models import Plan
from app.planner.planner import plan_turn

logger = get_logger(__name__)


def start(deps: FarmerContext, history: list, settings: PlannerSettings, original_query: Optional[str]) -> "asyncio.Task[Plan]":
    pairs = history_pairs_from_messages(history, settings.history_pairs)
    # Copy: the live turn mutates deps (SHC context, artifacts) while we plan.
    snapshot = deps.model_copy()
    return asyncio.create_task(plan_turn(snapshot, pairs, settings, original_query=original_query))


async def finish(task: "asyncio.Task[Plan]", *, new_messages: list, base: dict[str, Any]) -> None:
    try:
        plan = await asyncio.wait_for(task, timeout=15)
    except Exception as exc:
        logger.warning("shadow plan failed: %s", exc)
        return
    calls = legacy_tool_calls(new_messages)
    await tracestore.record(
        **base,
        arm="shadow",
        intent=plan.intent,
        tools_json=[{"name": c.name, "args": c.args, "confidence": c.confidence} for c in plan.tool_calls],
        plan_json={"answers": plan.answers, "notes": plan.compose_notes, "legacy_tools": calls, "escalate": plan.escalate, "reason": plan.escalate_reason},
        jev_ms=plan.jev_ms,
        jev_input_tokens=plan.jev_input_tokens,
        escalated=int(plan.escalate),
        agreement=agreement(calls, plan) if not plan.escalate else "n/a",
    )
