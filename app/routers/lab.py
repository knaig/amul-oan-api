"""Planner lab: trigger a chat message or a voice turn against BOTH agent-step
arms (legacy two-request LLM loop vs Jev plan + one compose request), stream the
answers side by side, and record every stage timing for comparison.

Dev/staging only (PLANNER_LAB_ENABLED; defaults on outside production).
Farmer identity is supplied explicitly (mobile) because there is no JWT here.
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents.deps import FarmerContext
from agents.farmer_context import get_farmer_context_bundle_by_mobile
from agents.tools.farmer import normalize_phone_to_mobile
from app.config import get_config_value, settings
from app.planner import jev, tracestore
from app.planner.arms import history_pairs_from_messages
from app.planner.config import PlannerSettings, lab_enabled
from app.planner.models import StageRecorder
from app.planner.planner import plan_turn
from app.planner.questions import ALL_TOOLS, TOOL_DESCRIPTIONS, gates_for
from app.planner.side_effects import DISABLED_TOOLS, DRY_RUN_SIDE_EFFECTS, SEARCH_TOP_K_OVERRIDE
from app.services.chat import stream_chat_messages
from app.utils import _get_message_history
from helpers.utils import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/lab", tags=["planner-lab"])

_STATIC = settings.base_dir / "app" / "static" / "lab.html"


def _guard() -> None:
    if not lab_enabled():
        raise HTTPException(status_code=404)


class TurnRequest(BaseModel):
    query: str
    arms: list[str] = Field(default_factory=lambda: ["llm", "jev"])
    session_id: Optional[str] = None
    source_lang: str = "gu"
    target_lang: str = "gu"
    channel: str = "web"
    persona: str = "farmer"
    mobile: Optional[str] = Field(None, description="Farmer mobile for real profile / tools (no JWT in the lab)")
    dry_run_side_effects: bool = True
    disabled_tools: list[str] = Field(default_factory=list)
    search_top_k: Optional[int] = None
    planner_overrides: dict[str, Any] = Field(default_factory=dict)
    persist: bool = True
    model_profile: Optional[str] = Field(None, description="Named pipeline profile (model set) for BOTH arms; see /lab/config profiles")


class VoiceTurnRequest(TurnRequest):
    query: str = ""
    audio_base64: str
    tts: bool = True


class RateRequest(BaseModel):
    trace_id: str
    rating: str
    note: str = ""


class PreviewRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    mobile: Optional[str] = None
    persona: str = "farmer"
    planner_overrides: dict[str, Any] = Field(default_factory=dict)
    disabled_tools: list[str] = Field(default_factory=list)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _user_info(mobile: Optional[str]) -> dict[str, Any]:
    phone = normalize_phone_to_mobile(mobile or "") if mobile else None
    return {"phone": phone, "sub": phone} if phone else {}


async def _run_arm(arm: str, req: TurnRequest, base_session: str, group: str, queue: asyncio.Queue) -> None:
    """One arm of one turn, in its own contextvar context (tool switches)."""
    DRY_RUN_SIDE_EFFECTS.set(req.dry_run_side_effects)
    DISABLED_TOOLS.set(frozenset(req.disabled_tools))
    SEARCH_TOP_K_OVERRIDE.set(req.search_top_k)
    session_id = f"{base_session}::{arm}"
    stages = StageRecorder()
    sink: dict[str, Any] = {"persist": req.persist}
    overrides = dict(req.planner_overrides)
    overrides["dry_run_side_effects"] = req.dry_run_side_effects
    overrides["disabled_tools"] = list(req.disabled_tools)
    if req.search_top_k:
        overrides["search_top_k"] = req.search_top_k
    bg = BackgroundTasks()
    try:
        history = await _get_message_history(session_id)
        await queue.put({"arm": arm, "type": "start", "session_id": session_id, "history_turns": len(history) // 2})
        gen = stream_chat_messages(
            query=req.query, session_id=session_id, source_lang=req.source_lang, target_lang=req.target_lang,
            channel=req.channel, user_id=(normalize_phone_to_mobile(req.mobile or "") or "lab") if req.mobile else "lab",
            history=history, user_info=_user_info(req.mobile), background_tasks=bg,
            persona=req.persona if req.persona in ("farmer", "doctor") else "farmer",
            planner=arm, planner_overrides=overrides, stages=stages, turn_sink=sink, compare_group=group,
            emit_artifact_frames=False, model_profile=req.model_profile,
        )
        plan_sent = False
        async for chunk in gen:
            if not plan_sent and sink.get("plan") is not None:
                plan_sent = True
                await queue.put({"arm": arm, "type": "plan", **_plan_event(sink["plan"], stages)})
            await queue.put({"arm": arm, "type": "token", "text": chunk, "t_ms": round(stages.elapsed_ms(), 1)})
        if not plan_sent and sink.get("plan") is not None:
            await queue.put({"arm": arm, "type": "plan", **_plan_event(sink["plan"], stages)})
        # Run FastAPI background tasks the service queued (suggestions, shadow).
        for task in bg.tasks:
            try:
                await task()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("lab background task failed: %s", exc)
        await queue.put({
            "arm": arm, "type": "done", "answer": sink.get("answer", ""), "answer_en": sink.get("answer_en", ""), "query_en": sink.get("query_en", ""), "stages": stages.snapshot(),
            "tools": sink.get("tools", []), "trace_id": sink.get("trace_id"), "session_id": session_id,
        })
    except Exception as exc:
        logger.exception("lab arm %s failed", arm)
        await queue.put({"arm": arm, "type": "error", "error": f"{type(exc).__name__}: {exc}", "stages": stages.snapshot()})


def _plan_event(plan: Any, stages: StageRecorder) -> dict[str, Any]:
    return {
        "intent": plan.intent,
        "tools": [{"name": c.name, "args": c.args, "confidence": round(c.confidence, 3), "source": c.source} for c in plan.tool_calls],
        "notes": plan.compose_notes,
        "escalate": plan.escalate,
        "escalate_reason": plan.escalate_reason,
        "confidence": round(plan.confidence, 3),
        "jev_ms": round(plan.jev_ms, 1),
        "jev_input_tokens": plan.jev_input_tokens,
        "jev_model": plan.jev_model,
        "answers": plan.answers,
        "t_ms": round(stages.elapsed_ms(), 1),
    }


async def _turn_events(req: TurnRequest, *, transcript: Optional[str] = None, tts: bool = False) -> AsyncIterator[str]:
    base_session = req.session_id or f"lab-{uuid.uuid4().hex[:8]}"
    group = uuid.uuid4().hex
    arms = [a for a in req.arms if a in ("llm", "jev")] or ["llm", "jev"]
    queue: asyncio.Queue = asyncio.Queue()
    yield _sse({"type": "meta", "compare_group": group, "base_session": base_session, "arms": arms, "transcript": transcript})
    tasks = [asyncio.create_task(_run_arm(arm, req, base_session, group, queue), context=contextvars.copy_context()) for arm in arms]
    finished = 0
    answers: dict[str, str] = {}
    while finished < len(tasks):
        event = await queue.get()
        if event.get("type") in ("done", "error"):
            finished += 1
            if event.get("type") == "done":
                answers[event["arm"]] = event.get("answer", "")
        yield _sse(event)
    if tts and answers:
        from helpers.tts import text_to_speech_bhashini_async

        for arm, text in answers.items():
            if not text.strip():
                continue
            try:
                t0 = asyncio.get_running_loop().time()
                audio = await text_to_speech_bhashini_async(text, req.target_lang, gender="female", sampling_rate=8000)
                yield _sse({"arm": arm, "type": "audio", "audio_base64": base64.b64encode(audio).decode("ascii") if isinstance(audio, (bytes, bytearray)) else audio,
                            "tts_ms": round((asyncio.get_running_loop().time() - t0) * 1000, 1)})
            except Exception as exc:
                yield _sse({"arm": arm, "type": "audio_error", "error": f"{type(exc).__name__}: {exc}"})
    yield _sse({"type": "end"})


@router.get("/")
async def lab_page():
    _guard()
    return FileResponse(str(_STATIC), media_type="text/html")


@router.get("/simple")
async def lab_simple_page():
    """Plain-language view: one question, two timelines, the saving highlighted."""
    _guard()
    return FileResponse(str(_STATIC.with_name("lab_simple.html")), media_type="text/html")


@router.get("/config")
async def lab_config():
    _guard()
    defaults = PlannerSettings.from_env().to_dict()
    checks = {
        "typesafe_api_key": bool(get_config_value("TYPESAFE_API_KEY")),
        "typesafe_sdk": jev.AsyncTypeSafeClient is not None,
        "openai_api_key": bool(get_config_value("OPENAI_API_KEY")),
        "anthropic_api_key": bool(get_config_value("ANTHROPIC_API_KEY")),
        "beckn_bridge": bool(get_config_value("BECKN_BAP_CALLER_URL")),
        "vistaar_seeker": bool(get_config_value("VISTAAR_SEEKER_URL") or get_config_value("VISTAAR_BAP_URL")),
        "loan_db": bool(get_config_value("LOAN_DB_URL")),
        "bhashini": bool(get_config_value("BHASHINI_API_KEY")),
        "langfuse": bool(get_config_value("LANGFUSE_PUBLIC_KEY")),
        "trace_db": True,
    }
    try:
        from app.core.cache import redis_client
        checks["redis"] = bool(await asyncio.wait_for(redis_client.ping(), 1.5))
    except Exception:
        checks["redis"] = False
    profiles = []
    try:
        from app.llm_core import runtime as _rt
        from app.llm_core.config_model import Step as _Step
        cfg = _rt.get_pipeline()
        for p in cfg.profiles:
            plan = cfg.step_plan(p, _Step.AGENT)
            profiles.append({"name": p.name, "weight": p.weight, "agent_model": f"{plan.tiers[0].provider.value}:{plan.tiers[0].model}" if plan else None})
    except Exception as exc:  # pragma: no cover
        logger.debug("lab config profiles unavailable: %s", exc)
    samples = []
    try:
        import json as _json
        for line in (settings.base_dir / "scripts" / "planner_eval_sample.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                samples.append(_json.loads(line))
    except Exception as exc:  # pragma: no cover
        logger.debug("lab samples unavailable: %s", exc)
    return {
        "defaults": defaults,
        "tools": [{"name": t, "description": TOOL_DESCRIPTIONS[t]} for t in ALL_TOOLS],
        "checks": checks,
        "profiles": profiles,
        "samples": samples,
        "environment": settings.environment,
        "api_prefix": settings.api_prefix,
    }


@router.post("/turn")
async def lab_turn(req: TurnRequest):
    _guard()
    return StreamingResponse(_turn_events(req), media_type="text/event-stream")


@router.post("/voice-turn")
async def lab_voice_turn(req: VoiceTurnRequest):
    """A voice-bot turn: transcribe -> both arms -> TTS of each answer."""
    _guard()
    from helpers.transcription import transcribe_bhashini_async

    async def events() -> AsyncIterator[str]:
        try:
            t0 = asyncio.get_running_loop().time()
            transcript = await transcribe_bhashini_async(req.audio_base64, req.source_lang)
            stt_ms = round((asyncio.get_running_loop().time() - t0) * 1000, 1)
        except Exception as exc:
            yield _sse({"type": "error", "error": f"transcription failed: {type(exc).__name__}: {exc}"})
            yield _sse({"type": "end"})
            return
        yield _sse({"type": "transcript", "text": transcript, "stt_ms": stt_ms})
        if not (transcript or "").strip():
            yield _sse({"type": "end"})
            return
        turn = TurnRequest(**{**req.model_dump(exclude={"audio_base64", "tts"}), "query": transcript})
        async for ev in _turn_events(turn, transcript=transcript, tts=req.tts):
            yield ev

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/plan-preview")
async def lab_plan_preview(req: PreviewRequest):
    """Run ONLY the Jev planning step: state, questions, answers, decoded plan."""
    _guard()
    planner_settings = PlannerSettings.from_env().merged({**req.planner_overrides, "disabled_tools": req.disabled_tools})
    farmer_info, unions, location = "", [], {}
    mobile = normalize_phone_to_mobile(req.mobile or "") if req.mobile else None
    if mobile:
        try:
            farmer_info, unions, location = await get_farmer_context_bundle_by_mobile(mobile)
        except Exception as exc:
            logger.warning("lab preview farmer context failed: %s", exc)
    deps = FarmerContext(query=req.query, session_id=req.session_id or "lab-preview", lang_code="en", farmer_info=farmer_info,
                         farmer_unions=unions, farmer_district=location.get("district") or None, mobile=mobile,
                         persona=req.persona if req.persona in ("farmer", "doctor") else "farmer")
    history = await _get_message_history(f"{req.session_id}::jev") if req.session_id else []
    pairs = history_pairs_from_messages(history, planner_settings.history_pairs)
    plan = await plan_turn(deps, pairs, planner_settings)
    gates = gates_for(deps, planner_settings)
    return {
        "state": plan.state, "questions": plan.questions, "answers": plan.answers,
        "plan": _plan_event(plan, StageRecorder()), "enabled_tools": gates.enabled_tools(),
        "settings": planner_settings.to_dict(),
    }


@router.get("/traces")
async def lab_traces(limit: int = 100, compare_group: Optional[str] = None):
    _guard()
    return await tracestore.recent(limit=limit, compare_group=compare_group)


@router.get("/stats")
async def lab_stats():
    _guard()
    return await tracestore.stats()


@router.post("/rate")
async def lab_rate(req: RateRequest):
    _guard()
    if req.rating not in ("correct", "wrong", "better", "worse", ""):
        raise HTTPException(status_code=422, detail="rating must be correct|wrong|better|worse")
    ok = await tracestore.rate(req.trace_id, req.rating, req.note)
    return JSONResponse({"ok": ok})
