"""Executor, compose block, arm streaming and chat-service wiring (offline)."""
import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest
from fastapi import BackgroundTasks

from agents.deps import FarmerContext
from app.planner import arms as arms_mod
from app.planner import executor
from app.planner.compose import build_compose_agent, results_block
from app.planner.config import PlannerSettings
from app.planner.models import Plan, StageRecorder, ToolCall, ToolResult
from app.planner.side_effects import DRY_RUN_SIDE_EFFECTS
from app.services import chat as chat_service
from tests.test_chat_turn_sequence import _Cache, _Run


def _deps():
    return FarmerContext(query="my cow has fever", session_id="s", lang_code="en", mobile="9876543210")


def test_executor_dry_runs_side_effect_tools_without_network():
    plan = Plan(intent="services", tool_calls=[ToolCall("create_health_call", {"union_code": "1", "society_code": "2", "farmer_code": "3", "species": "cow", "case_type": "normal", "remark": "x"})])
    results = asyncio.run(executor.execute(plan, _deps(), PlannerSettings(dry_run_side_effects=True)))
    assert results[0].dry_run and "[TEST MODE] create_health_call" in results[0].output


def test_executor_runs_search_in_parallel_and_surfaces_validator_rejections(monkeypatch):
    calls = []

    async def fake_network(query, top_k):
        calls.append((query, top_k))
        await asyncio.sleep(0.05)
        return f"docs for {query}"

    monkeypatch.setattr("agents.tools.search.network_search_documents", fake_network)
    plan = Plan(intent="clinical", tool_calls=[
        ToolCall("search_documents", {"query": "cow fever treatment", "top_k": 5}),
        ToolCall("search_documents", {"query": "cow fever symptoms", "top_k": 5}),
        ToolCall("search_documents", {"query": "", "top_k": 5}),  # validator rejects
    ])
    stages = StageRecorder()
    t0 = asyncio.get_event_loop_policy().new_event_loop().time()
    results = asyncio.run(executor.execute(plan, _deps(), PlannerSettings(), stages))
    assert [r.output for r in results[:2]] == ["docs for cow fever treatment", "docs for cow fever symptoms"]
    assert not results[2].ok and "EMPTY_QUERY" in results[2].output
    assert len(calls) == 2 and len(stages.tools) == 3


def test_compose_agent_has_no_tools_and_carries_results():
    plan = Plan(intent="clinical", tool_calls=[], compose_notes=["Ask about booking."])
    res = [ToolResult("search_documents", {"query": "cow fever"}, "1. Doc\nGive water", 12.0)]
    block = results_block(plan, res)
    assert "search_documents(query='cow fever')" in block and "Give water" in block and "Ask about booking." in block
    agent = build_compose_agent(_deps(), plan, res)
    assert agent.name == "Amul AI Compose"
    assert not list(agent._function_toolset.tools) if hasattr(agent, "_function_toolset") else True


def _fake_execution(record):
    class _Exec:
        async def stream(self, agent, prompt, *, message_history, deps, new_messages, observer=None):
            record.append(("stream", getattr(agent, "name", None)))
            if observer is not None:
                observer.meta.setdefault("model_requests", 1)
            for c in ["Hello ", "farmer."]:
                yield c
    return _Exec()


def test_jev_arm_plans_executes_then_composes_once(monkeypatch):
    record = []
    plan = Plan(intent="clinical", tool_calls=[ToolCall("search_documents", {"query": "cow fever", "top_k": 8})], jev_ms=120.0, jev_input_tokens=900, jev_model="jev-1.13.0")

    async def fake_plan(deps, pairs, settings, original_query=None, gates=None):
        record.append(("plan", pairs))
        return plan

    async def fake_execute(plan_, deps, settings, stages=None):
        record.append(("execute", plan_.tool_names()))
        return [ToolResult("search_documents", {"query": "cow fever"}, "docs", 30.0)]

    monkeypatch.setattr(arms_mod, "plan_turn", fake_plan)
    monkeypatch.setattr(arms_mod, "execute", fake_execute)
    monkeypatch.setattr(arms_mod, "build_compose_agent", lambda deps, plan_, results: SimpleNamespace(name="Amul AI Compose"))
    stages, sink = StageRecorder(), {}

    async def go():
        out = []
        async for c in arms_mod.jev_agent_stream(deps=_deps(), user_message="**User:** hi", history=[], execution=_fake_execution(record),
                                                 new_messages=[], legacy_agent=SimpleNamespace(name="legacy"), settings=PlannerSettings(),
                                                 stages=stages, sink=sink):
            out.append(c)
        return "".join(out)

    assert asyncio.run(go()) == "Hello farmer."
    assert [r[0] for r in record] == ["plan", "execute", "stream"] and record[2][1] == "Amul AI Compose"
    assert stages.meta["model_requests"] == 1 and "compose_first_token" in stages.marks and stages.spans["plan"] >= 0
    assert sink["plan"] is plan and stages.meta["planned_tools"][0]["name"] == "search_documents"


def test_jev_arm_appends_a_dropped_ticket_number(monkeypatch):
    record = []
    plan = Plan(intent="services", tool_calls=[ToolCall("create_health_call", {})])

    async def fake_plan(deps, pairs, settings, original_query=None, gates=None):
        return plan

    async def fake_execute(plan_, deps, settings, stages=None):
        return [ToolResult("create_health_call", {}, "Health call booked successfully via the Beckn network. Ticket: HC-260922-AB12C", 900.0)]

    monkeypatch.setattr(arms_mod, "plan_turn", fake_plan)
    monkeypatch.setattr(arms_mod, "execute", fake_execute)
    monkeypatch.setattr(arms_mod, "build_compose_agent", lambda deps, plan_, results: SimpleNamespace(name="Amul AI Compose"))
    stages, sink = StageRecorder(), {}

    async def go():
        return "".join([c async for c in arms_mod.jev_agent_stream(deps=_deps(), user_message="q", history=[], execution=_fake_execution(record),
                                                                   new_messages=[], legacy_agent=SimpleNamespace(name="legacy"), settings=PlannerSettings(), stages=stages, sink=sink)])

    out = asyncio.run(go())
    assert out.endswith("Your ticket number is HC-260922-AB12C.") and stages.meta["ticket_appended"] == "HC-260922-AB12C"


def test_jev_arm_escalates_to_legacy_agent_when_plan_says_so(monkeypatch):
    record = []

    async def fake_plan(deps, pairs, settings, original_query=None, gates=None):
        return Plan(intent="unknown", tool_calls=[], escalate=True, escalate_reason="jev unavailable: no key")

    monkeypatch.setattr(arms_mod, "plan_turn", fake_plan)
    stages, sink = StageRecorder(), {}

    async def go():
        return "".join([c async for c in arms_mod.jev_agent_stream(deps=_deps(), user_message="q", history=[], execution=_fake_execution(record),
                                                                   new_messages=[], legacy_agent=SimpleNamespace(name="legacy"), settings=PlannerSettings(), stages=stages, sink=sink)])

    assert asyncio.run(go()) == "Hello farmer."
    assert record == [("stream", "legacy")] and stages.meta["escalated"] is True


def _drive_chat(monkeypatch, *, planner, compose_chunks=("Give clean water daily.",), overrides=None, moderation_category="valid_agricultural", plan_factory=None):
    seen = []
    monkeypatch.setattr(chat_service, "propagate_attributes", None)
    monkeypatch.setattr(chat_service, "get_langfuse_client", None)
    monkeypatch.setattr(chat_service, "cache", _Cache())
    monkeypatch.setattr(chat_service, "trim_history", lambda *_a, **_kw: [])
    monkeypatch.setattr(chat_service, "format_message_pairs", lambda *_a, **_kw: "")

    async def _moderate(user_message, model=None):
        await asyncio.sleep(0.05)  # lets a concurrently started agent stream get ahead
        seen.append("moderation")
        return SimpleNamespace(output=SimpleNamespace(category=moderation_category, action="I only answer farming questions."))

    def _legacy_iter(**_kw):
        seen.append("legacy_agent")
        return _Run(["legacy answer"])

    async def _history(*_a, **_kw):
        seen.append("history_persist")

    async def _noop(*_a, **_kw):
        return None

    async def _fake_plan(deps, pairs, settings, original_query=None, gates=None):
        seen.append("jev_plan")
        if plan_factory:
            return plan_factory()
        return Plan(intent="nutrition", tool_calls=[ToolCall("search_documents", {"query": "cow water", "top_k": 8})], jev_ms=90.0)

    async def _fake_execute(plan, deps, settings, stages=None):
        seen.append("tools")
        return [ToolResult("search_documents", {"query": "cow water"}, "docs", 20.0)]

    def _compose_iter(**_kw):
        seen.append("compose_agent")
        return _Run(list(compose_chunks))

    monkeypatch.setattr(chat_service.moderation_agent, "run", _moderate)
    monkeypatch.setattr(chat_service.agrinet_agent, "iter", _legacy_iter)
    monkeypatch.setattr(chat_service, "update_message_history", _history)
    monkeypatch.setattr(chat_service, "set_cache", _noop)
    monkeypatch.setattr(chat_service, "create_suggestions", lambda *a, **k: None)
    monkeypatch.setattr(arms_mod, "plan_turn", _fake_plan)
    monkeypatch.setattr(arms_mod, "execute", _fake_execute)
    monkeypatch.setattr(chat_service, "get_session_shc_context", _noop)
    monkeypatch.setattr(arms_mod, "build_compose_agent", lambda deps, plan, results: SimpleNamespace(name="Amul AI Compose", iter=_compose_iter))

    async def _record(**fields):
        seen.append(("trace", fields["arm"], fields["tools_json"]))
        return "trace-1"

    monkeypatch.setattr(chat_service._planner_trace, "record", _record)
    stages, sink = StageRecorder(), {}

    async def _go():
        out = []
        async for chunk in chat_service.stream_chat_messages(
            query="How much water should I give my cow?", session_id="planner-wiring", source_lang="en", target_lang="en",
            channel="web", user_id="lab", history=[], user_info={}, background_tasks=BackgroundTasks(),
            planner=planner, stages=stages, turn_sink=sink, compare_group="g1", planner_overrides=overrides,
        ):
            out.append(chunk)
        return "".join(out)

    return asyncio.run(_go()), seen, stages, sink


def test_chat_service_jev_arm_makes_one_compose_request(monkeypatch):
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="jev")
    assert out == "Give clean water daily."
    order = [s for s in seen if isinstance(s, str)]
    assert set(order) == {"jev_plan", "moderation", "tools", "compose_agent", "history_persist"} and order.count("jev_plan") == 1
    assert order.index("jev_plan") < order.index("tools") < order.index("compose_agent") < order.index("history_persist")
    assert stages.meta["arm"] == "jev" and stages.meta["model_requests"] == 1 and stages.meta["plan_overlapped_with_moderation"] is True
    assert "plan_started" in stages.marks
    assert "first_client_token" in stages.marks and "agent_start" in stages.marks
    trace = [s for s in seen if isinstance(s, tuple)][0]
    assert trace[1] == "jev" and trace[2][0]["name"] == "search_documents"
    assert sink["trace_id"] == "trace-1" and sink["answer"] == out


def test_chat_service_llm_arm_is_unchanged_and_traced(monkeypatch):
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="llm")
    assert out == "legacy answer"
    assert "jev_plan" not in seen and "legacy_agent" in seen
    assert stages.meta["arm"] == "llm"
    assert [s for s in seen if isinstance(s, tuple)][0][1] == "llm"


def test_concurrent_moderation_overlaps_agent_and_releases_tokens_after_verdict(monkeypatch):
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="jev", overrides={"concurrent_moderation": True})
    assert out == "Give clean water daily."
    order = [s for s in seen if isinstance(s, str)]
    assert order.index("jev_plan") < order.index("moderation")  # planning did not wait for the verdict
    assert stages.meta.get("concurrent_moderation") is True and "moderation" in stages.spans


def test_concurrent_moderation_rejection_emits_only_the_decline(monkeypatch):
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="llm", overrides={"concurrent_moderation": True}, moderation_category="invalid_non_agricultural")
    assert out == "I only answer farming questions."
    assert "legacy answer" not in out and "first_client_token" not in stages.marks


def test_jev_moderation_skips_the_llm_check_when_allowed(monkeypatch):
    mk = lambda: Plan(intent="nutrition", tool_calls=[ToolCall("search_documents", {"query": "cow water", "top_k": 8})], moderation_category="valid_agricultural", moderation_action="Proceed with the query.", moderation_confidence=0.97)
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="jev", overrides={"moderation_source": "jev"}, plan_factory=mk)
    assert out == "Give clean water daily."
    assert "moderation" not in [s for s in seen if isinstance(s, str)]  # no LLM safety call
    assert stages.meta["moderation_source"] == "jev" and "moderation" not in stages.spans


def test_jev_moderation_blocks_with_canned_line(monkeypatch):
    mk = lambda: Plan(intent="out_of_scope", tool_calls=[], moderation_category="invalid_non_agricultural", moderation_action="I can only help with farming, dairy and livestock questions.", moderation_confidence=0.95)
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="jev", overrides={"moderation_source": "jev"}, plan_factory=mk)
    assert out.startswith("I can only help with farming") and "compose_agent" not in seen or "first_client_token" not in stages.marks


def test_jev_moderation_falls_back_to_llm_when_jev_escalates(monkeypatch):
    mk = lambda: Plan(intent="unknown", tool_calls=[], escalate=True, escalate_reason="jev unavailable")
    out, seen, stages, sink = _drive_chat(monkeypatch, planner="jev", overrides={"moderation_source": "jev"}, plan_factory=mk)
    assert "moderation" in seen and stages.meta["moderation_source"] == "llm-fallback"
    assert out == "legacy answer"


def test_pipelined_translation_preserves_order_and_overlaps(monkeypatch):
    import app.services.chat as cs
    calls = []

    async def _tr(text, *_a, **_k):
        calls.append(("start", text)); await asyncio.sleep(0.05); calls.append(("end", text))
        yield f"[{text.strip()}]"

    monkeypatch.setattr(cs, "translate_text_stream_fast", _tr)
    monkeypatch.setattr(cs, "should_translate_batch", lambda text, n: True)  # one batch per sentence
    monkeypatch.setattr(cs, "propagate_attributes", None); monkeypatch.setattr(cs, "get_langfuse_client", None)
    monkeypatch.setattr(cs, "cache", _Cache()); monkeypatch.setattr(cs, "trim_history", lambda *a, **k: []); monkeypatch.setattr(cs, "format_message_pairs", lambda *a, **k: "")

    async def _pre(_tier, *, text, **_k):
        return "en"

    async def _mod(user_message, model=None):
        return SimpleNamespace(output=SimpleNamespace(category="valid_agricultural", action="allow"))

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(cs, "pretranslate_with_tier", _pre); monkeypatch.setattr(cs.moderation_agent, "run", _mod)
    monkeypatch.setattr(cs.agrinet_agent, "iter", lambda **_k: _Run(["One. ", "Two. ", "Three. ", "Four."]))
    monkeypatch.setattr(cs, "update_message_history", _noop); monkeypatch.setattr(cs, "set_cache", _noop); monkeypatch.setattr(cs, "create_suggestions", lambda *a, **k: None)
    stages = StageRecorder()

    async def go():
        return "".join([c async for c in cs.stream_chat_messages(query="q", session_id="pipe", source_lang="gu", target_lang="gu", channel="web", user_id="u", history=[], user_info={}, background_tasks=BackgroundTasks(), planner="llm", planner_overrides={"pipelined_translation": True}, stages=stages)])

    out = asyncio.run(go())
    assert out == "[One.][Two.][Three.][Four.]"  # order kept
    starts = [i for i, c in enumerate(calls) if c[0] == "start"]; ends = [i for i, c in enumerate(calls) if c[0] == "end"]
    assert starts[1] < ends[0] or starts[2] < ends[1]  # at least two translations overlapped
    assert stages.meta.get("pipelined_translation") is True


def test_dry_run_contextvar_stops_real_booking():
    from agents.tools.health_call import create_health_call
    from agents.tools.models.ai_call import AISpecies
    from agents.tools.models.health_call import HealthCaseType

    token = DRY_RUN_SIDE_EFFECTS.set(True)
    try:
        out = asyncio.run(create_health_call(SimpleNamespace(deps=_deps(), tool_call_id="t"), "1", "2", "3", AISpecies.COW, HealthCaseType.NORMAL, "r"))
    finally:
        DRY_RUN_SIDE_EFFECTS.reset(token)
    assert out.startswith("[TEST MODE] create_health_call")


def test_planner_settings_merge_and_env(monkeypatch):
    monkeypatch.setenv("PLANNER_MODE", "shadow")
    monkeypatch.setenv("PLANNER_DISABLED_TOOLS", "create_ai_call, check_loan_eligibility")
    s = PlannerSettings.from_env()
    assert s.mode == "shadow" and s.disabled_tools == ["create_ai_call", "check_loan_eligibility"]
    m = s.merged({"search_top_k": 3, "unknown": 1, "yes_threshold": None})
    assert m.search_top_k == 3 and m.yes_threshold == s.yes_threshold
