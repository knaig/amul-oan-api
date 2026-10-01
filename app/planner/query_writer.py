"""Search-query writer: one short generative call, run alongside the Jev plan.

Jev routes and fills slots but cannot write text. Retrieval needs a query that
resolves follow-ups ("and for a buffalo?" after a mastitis question) and uses the
documents' vocabulary ("low fat SNF" for "thin, watery milk"). This module asks
the deployment's own fast model (the MODERATION step: on-prem Gemma in
production) for that query, with a few-line prompt instead of the agent's full
prompt and tool schemas. It starts at the same moment as the Jev plan, so on a
search turn its latency overlaps the plan; on any other turn it is cancelled.
The keyword query from ``keywords.py`` stays as the fallback and second variant.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

from pydantic_ai import Agent, ModelRetry

from agents.tools.search import _validate_search_query
from app.llm_core import Step
from app.planner.models import Plan, StageRecorder, ToolCall
from helpers.utils import get_logger

logger = get_logger(__name__)

QUERY_PROMPT = (
    "You write the search query for a veterinary, dairy and agriculture document library.\n"
    "Read the farmer's latest message in the light of the conversation and output ONE query:\n"
    "- 3 to 8 English keywords, no sentence, no punctuation, no quotes\n"
    "- name the animal, the problem and what is wanted (for example: treatment, feeding, prevention)\n"
    "- resolve follow-ups from the conversation (\"and for a buffalo?\" keeps the earlier problem)\n"
    "- prefer standard veterinary and agronomy terms over the farmer's everyday words\n"
    "Output only the query."
)

_writer = Agent(model=None, name="Amul Search Query Writer", instrument=True, output_type=str,
                retries=0, instructions=QUERY_PROMPT)


def build_prompt(message: str, history_pairs: list[tuple[str, str]]) -> str:
    lines = []
    for farmer, assistant in history_pairs:
        lines.append(f"Farmer: {farmer}")
        lines.append(f"Assistant: {assistant[:400]}")
    convo = "\n".join(lines) if lines else "(none)"
    return f"Conversation so far:\n{convo}\n\nFarmer's latest message: {message}"


def clean(text: str) -> Optional[str]:
    """A usable query, or None (the caller falls back to the keyword query)."""
    lines = (text or "").strip().splitlines()
    query = re.sub(r"[\"'`*:;,.?!]", " ", lines[0] if lines else "")
    query = " ".join(query.split()[:12])
    if not query:
        return None
    try:
        return _validate_search_query(query)
    except ModelRetry:
        return None


async def write_query(execution: Any, message: str, history_pairs: list[tuple[str, str]]) -> dict[str, Any]:
    t0 = time.monotonic()
    run = await execution.run(Step.MODERATION, _writer, build_prompt(message, history_pairs))
    return {"query": clean(str(run.output)), "raw": str(run.output)[:200], "ms": (time.monotonic() - t0) * 1000.0}


def start(execution: Any, message: str, history_pairs: list[tuple[str, str]]) -> "asyncio.Task[dict[str, Any]]":
    return asyncio.create_task(write_query(execution, message, history_pairs))


async def apply(plan: Plan, task: Optional["asyncio.Task"], *, wait_s: float, top_k: int,
                stages: StageRecorder) -> None:
    """Put the written query first among the plan's searches; keep one keyword variant.

    No search planned -> the task is cancelled. Late (beyond ``wait_s`` after the
    plan is ready), failed or invalid -> the keyword queries stand unchanged."""
    if task is None:
        return
    searches = [c for c in plan.tool_calls if c.name == "search_documents"]
    if not searches:
        task.cancel()
        return
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
    except asyncio.TimeoutError:
        task.cancel()
        stages.meta["query_writer"] = {"used": False, "reason": f"late (> {wait_s}s after the plan)"}
        return
    except Exception as exc:
        stages.meta["query_writer"] = {"used": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
        return
    waited = (time.monotonic() - t0) * 1000.0
    query = result.get("query")
    info = {"ms": round(result.get("ms", 0.0), 1), "waited_after_plan_ms": round(waited, 1), "raw": result.get("raw")}
    if not query:
        stages.meta["query_writer"] = {**info, "used": False, "reason": "invalid query"}
        return
    keyword = [c for c in searches if c.args.get("query", "").lower() != query.lower()]
    first = searches[0]
    written = ToolCall(name="search_documents", args={"query": query, "top_k": first.args.get("top_k", top_k)},
                       confidence=first.confidence, source="query_writer")
    kept = [written, *keyword[:max(0, len(searches) - 1)]]
    calls: list[ToolCall] = []
    for call in plan.tool_calls:
        if call is first:
            calls.extend(kept)
        elif call.name != "search_documents":
            calls.append(call)
    plan.tool_calls = calls
    stages.meta["query_writer"] = {**info, "used": True, "query": query}
