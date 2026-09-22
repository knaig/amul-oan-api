"""The single generative call: same persona prompt, tool results supplied, no tools."""
from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from agents.agrinet import _agrinet_max_output_tokens
from agents.deps import FarmerContext
from agents.doctor import _doctor_max_output_tokens
from agents.tools.terms import get_ambiguity_hints_for_query
from app.config import settings as app_settings
from helpers.utils import get_prompt, get_today_date_str
from app.planner.models import Plan, ToolResult


def base_instructions(deps: FarmerContext) -> str:
    if deps.persona == "doctor":
        return get_prompt("doctor_system_translation_pipeline.md", context={"today_date": get_today_date_str()})
    farmer_context = deps.get_farmer_context_string()
    hints = get_ambiguity_hints_for_query(deps.query)
    return get_prompt("agrinet_system_translation_pipeline.md", context={
        "today_date": get_today_date_str(),
        "farmer_context": farmer_context or None,
        "ambiguity_hints": hints or None,
        "response_max_chars": deps.get_response_max_chars(),
        "loan_max_amount": f"{int(app_settings.loan_max_amount):,}",
        "loan_interest_rate_pct": f"{int(app_settings.loan_interest_rate_pct)}",
        "network_tools_enabled": True,
        "vistaar_shc_enabled": app_settings.vistaar_shc_enabled,
    })


def results_block(plan: Plan, results: list[ToolResult]) -> str:
    lines = [
        "",
        "## Tool results for this turn (already executed by the system)",
        "The routing and tool calls for this message were decided and executed before you were called. "
        "You cannot call tools. Answer the farmer from the results below and the rules above. "
        "Do not mention tools, planners, or that anything was executed.",
    ]
    if results:
        for i, r in enumerate(results, 1):
            args = ", ".join(f"{k}={v!r}" for k, v in r.args.items() if k not in ("remark",))
            status = "" if r.ok else " (FAILED)"
            lines.append(f"\n### Result {i}: {r.name}({args}){status}\n{r.output.strip()}")
    else:
        lines.append("\nNo tool was needed for this message.")
    if plan.compose_notes:
        lines.append("\n## Notes for this turn")
        lines.extend(f"- {n}" for n in plan.compose_notes)
    return "\n".join(lines)


def build_compose_agent(deps: FarmerContext, plan: Plan, results: list[ToolResult]) -> Agent:
    instructions = base_instructions(deps) + "\n" + results_block(plan, results)
    max_tokens = _doctor_max_output_tokens() if deps.persona == "doctor" else _agrinet_max_output_tokens()
    return Agent(
        model=None,
        name="Amul AI Compose",
        instrument=True,
        output_type=str,
        deps_type=FarmerContext,
        retries=2,
        tools=[],
        instructions=instructions,
        model_settings=ModelSettings(max_tokens=max_tokens),
    )
