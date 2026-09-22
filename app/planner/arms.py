"""The two agent-step arms behind app.services.chat.stream_chat_messages.

* llm arm  : the unchanged pydantic-ai loop (request #1 tools -> request #2 compose).
* jev arm  : Jev plan -> direct tool execution -> ONE compose request.

Both yield English text chunks; timings and the plan land on the StageRecorder
so the service can persist a side-by-side trace.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from agents.deps import FarmerContext
from helpers.utils import get_logger
from app.planner.compose import build_compose_agent
from app.planner.config import PlannerSettings
from app.planner.executor import execute
from app.planner.models import Plan, StageRecorder, ToolResult
from app.planner.planner import plan_turn

logger = get_logger(__name__)


def history_pairs_from_messages(history: list, limit: int) -> list[tuple[str, str]]:
    from app.utils import get_message_pairs

    pairs = get_message_pairs(history, limit)  # newest first
    out = [(str(u.content), str(a.content)) for u, a in pairs]
    out.reverse()
    return out


async def jev_agent_stream(
    *,
    deps: FarmerContext,
    user_message: str,
    history: list,
    execution: Any,
    new_messages: list,
    legacy_agent: Any,
    settings: PlannerSettings,
    stages: StageRecorder,
    sink: dict[str, Any],
    original_query: Optional[str] = None,
) -> AsyncIterator[str]:
    """Drop-in for ``execution.stream(agent, ...)`` on the Jev arm."""
    stages.start("plan")
    plan_task = sink.pop("plan_task", None)
    if plan_task is not None:
        # Planning started before moderation (see app.services.chat); only the
        # remaining wait is on the critical path.
        plan = await plan_task
    else:
        pairs = history_pairs_from_messages(history, settings.history_pairs)
        plan = await plan_turn(deps, pairs, settings, original_query=original_query)
    stages.end("plan")
    stages.meta["plan_overlapped_with_moderation"] = plan_task is not None
    stages.meta.update({
        "arm": "jev", "intent": plan.intent, "plan_confidence": round(plan.confidence, 3),
        "jev_ms": round(plan.jev_ms, 1), "jev_input_tokens": plan.jev_input_tokens, "jev_model": plan.jev_model,
        "planned_tools": [{"name": c.name, "args": c.args, "confidence": round(c.confidence, 3)} for c in plan.tool_calls],
        "compose_notes": plan.compose_notes, "escalated": plan.escalate, "escalate_reason": plan.escalate_reason,
    })
    sink["plan"] = plan

    if plan.escalate:
        # Accuracy floor: the legacy two-request loop answers this turn.
        logger.info("jev arm escalating to legacy planner: %s", plan.escalate_reason)
        stages.start("compose")
        first = True
        async for chunk in execution.stream(legacy_agent, user_message, message_history=history, deps=deps,
                                            new_messages=new_messages, observer=stages):
            if first:
                stages.mark("compose_first_token")
                first = False
            yield chunk
        stages.end("compose")
        return

    stages.start("tools")
    results: list[ToolResult] = await execute(plan, deps, settings, stages)
    stages.end("tools")
    if results and not stages.tools:
        for r in results:
            stages.tool(r.name, r.args, r.ms, ok=r.ok, dry_run=r.dry_run, output_preview=r.output)
    sink["results"] = results

    agent = build_compose_agent(deps, plan, results)
    stages.start("compose")
    stages.meta["model_requests"] = 1
    first = True
    async for chunk in execution.stream(agent, user_message, message_history=history, deps=deps,
                                        new_messages=new_messages, observer=stages):
        if first:
            stages.mark("compose_first_token")
            first = False
        yield chunk
    stages.end("compose")


def start_plan_early(deps: FarmerContext, history: list, settings: PlannerSettings, original_query: Optional[str]):
    """Kick off the Jev plan concurrently with moderation. Safe: planning reads
    state and calls no tool; execution still waits for the moderation verdict."""
    import asyncio

    pairs = history_pairs_from_messages(history, settings.history_pairs)
    return asyncio.create_task(plan_turn(deps, pairs, settings, original_query=original_query))


def legacy_tool_calls(new_messages: list) -> list[dict[str, Any]]:
    """Tool calls the legacy loop actually made this turn (from pydantic-ai messages)."""
    calls: list[dict[str, Any]] = []
    for msg in new_messages or []:
        for part in getattr(msg, "parts", []):
            if getattr(part, "part_kind", "") == "tool-call":
                args = part.args
                if isinstance(args, str):
                    try:
                        import json
                        args = json.loads(args)
                    except Exception:
                        pass
                calls.append({"name": part.tool_name, "args": args})
    return calls


def agreement(legacy_calls: list[dict[str, Any]], plan: Plan) -> str:
    a = {c["name"] for c in legacy_calls}
    b = set(plan.tool_names())
    if a == b:
        return "same"
    if a <= b or b <= a:
        return "subset"
    return "different"
