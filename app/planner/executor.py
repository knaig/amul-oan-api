"""Execute a Plan's tool calls directly (no model in the loop), in parallel."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from pydantic_ai import ModelRetry

from agents.deps import FarmerContext
from agents.tools.ai_call import create_ai_call
from agents.tools.bonus import get_farmer_bonus_amount
from agents.tools.health_call import create_health_call
from agents.tools.loan import check_loan_eligibility
from agents.tools.milk_collection import get_farmer_milk_collection_details
from agents.tools.models.ai_call import AISpecies
from agents.tools.models.health_call import HealthCaseType
from agents.tools.search import search_documents
from agents.tools.union_schemes import get_union_scheme_data
from agents.tools.vet_offices import find_nearby_vet_offices
from agents.tools.vistaar import get_vistaar_mandi_prices, get_vistaar_scheme_info, get_vistaar_weather
from agents.tools.vistaar_shc import get_vistaar_soil_health_card
from helpers.utils import get_logger
from app.planner.config import PlannerSettings
from app.planner.models import SIDE_EFFECT_TOOLS, Plan, StageRecorder, ToolCall, ToolResult
from app.planner.side_effects import DRY_RUN_SIDE_EFFECTS, dry_run_message

logger = get_logger(__name__)

# name -> (callable, takes_ctx)
REGISTRY: dict[str, tuple[Any, bool]] = {
    "search_documents": (search_documents, False),
    "find_nearby_vet_offices": (find_nearby_vet_offices, True),
    "create_ai_call": (create_ai_call, True),
    "create_health_call": (create_health_call, True),
    "get_farmer_milk_collection_details": (get_farmer_milk_collection_details, True),
    "get_farmer_bonus_amount": (get_farmer_bonus_amount, True),
    "get_union_scheme_data": (get_union_scheme_data, True),
    "check_loan_eligibility": (check_loan_eligibility, True),
    "get_vistaar_weather": (get_vistaar_weather, True),
    "get_vistaar_mandi_prices": (get_vistaar_mandi_prices, True),
    "get_vistaar_scheme_info": (get_vistaar_scheme_info, False),
    "get_vistaar_soil_health_card": (get_vistaar_soil_health_card, True),
}


def _coerce(name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    if name in ("create_ai_call", "create_health_call") and isinstance(out.get("species"), str):
        out["species"] = AISpecies(out["species"])
    if name == "create_health_call" and isinstance(out.get("case_type"), str):
        out["case_type"] = HealthCaseType(out["case_type"])
    return out


async def run_one(call: ToolCall, deps: FarmerContext, index: int, settings: PlannerSettings) -> ToolResult:
    fn, takes_ctx = REGISTRY[call.name]
    dry = call.name in SIDE_EFFECT_TOOLS and (settings.dry_run_side_effects or DRY_RUN_SIDE_EFFECTS.get())
    t0 = time.monotonic()
    if dry:
        return ToolResult(call.name, call.args, dry_run_message(call.name, call.args), 0.0, ok=True, dry_run=True)
    # Mirrors pydantic_ai.RunContext for the fields the tools read.
    ctx = SimpleNamespace(deps=deps, tool_call_id=f"jev-{deps.session_id or 'anon'}-{index}", retry=0)
    try:
        kwargs = _coerce(call.name, call.args)
        output = await (fn(ctx, **kwargs) if takes_ctx else fn(**kwargs))
        ok = True
    except ModelRetry as exc:
        # The legacy loop would re-prompt the model; here the validator message is the result.
        output, ok = f"Tool rejected the arguments: {exc}", False
    except Exception as exc:  # a failed tool is a result the compose model must explain
        logger.exception("planner tool %s failed", call.name)
        output, ok = f"{call.name} failed: {type(exc).__name__}: {exc}", False
    return ToolResult(call.name, call.args, str(output), (time.monotonic() - t0) * 1000.0, ok=ok)


async def execute(plan: Plan, deps: FarmerContext, settings: PlannerSettings, stages: StageRecorder | None = None) -> list[ToolResult]:
    if not plan.tool_calls:
        return []
    results = await asyncio.gather(*(run_one(c, deps, i, settings) for i, c in enumerate(plan.tool_calls)))
    if stages is not None:
        for r in results:
            stages.tool(r.name, r.args, r.ms, ok=r.ok, dry_run=r.dry_run, output_preview=r.output)
    return list(results)
