"""Side-by-side turn traces (SQLAlchemy async; SQLite by default, Postgres via
PLANNER_TRACE_DB_URL). One row per (turn, arm). Never raises into a turn."""
from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select, text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from helpers.utils import get_logger
from app.planner.config import trace_db_url

logger = get_logger(__name__)
Base = declarative_base()


class TurnRow(Base):
    __tablename__ = "planner_turns"

    id = Column(String(40), primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    compare_group = Column(String(80), index=True)   # same user message across arms
    session_id = Column(String(200), index=True)
    arm = Column(String(16), index=True)             # llm | jev | shadow
    persona = Column(String(16))
    channel = Column(String(16))
    source_lang = Column(String(16))
    target_lang = Column(String(16))
    query = Column(Text)
    answer = Column(Text)
    intent = Column(String(40))
    tools_json = Column(Text)        # [{name,args,ms,ok,dry_run}]
    plan_json = Column(Text)         # jev answers + notes (jev/shadow only)
    stages_json = Column(Text)       # StageRecorder snapshot
    ttft_ms = Column(Float)          # first token to client (after moderation etc.)
    total_ms = Column(Float)
    jev_ms = Column(Float)
    jev_input_tokens = Column(Integer)
    model_requests = Column(Integer) # generative model requests made in the agent step
    escalated = Column(Integer, default=0)
    agreement = Column(String(16))   # shadow: same | subset | different | n/a
    error = Column(Text)
    rating = Column(String(16))      # manual: correct | wrong | better | worse
    rating_note = Column(Text)


_engine = None
_sessions: Optional[async_sessionmaker] = None
_ready = False


async def _ensure() -> Optional[async_sessionmaker]:
    global _engine, _sessions, _ready
    if _sessions is not None and _ready:
        return _sessions
    try:
        _engine = create_async_engine(trace_db_url(), pool_pre_ping=True)
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _sessions = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
        _ready = True
        return _sessions
    except Exception as exc:
        logger.warning("planner trace store unavailable: %s", exc)
        return None


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


async def record(**fields: Any) -> Optional[str]:
    sessions = await _ensure()
    if sessions is None:
        return None
    row_id = uuid.uuid4().hex
    row = TurnRow(id=row_id, ts=datetime.now(timezone.utc))
    for k, v in fields.items():
        if k in ("tools_json", "plan_json", "stages_json") and not isinstance(v, str):
            v = _dumps(v)
        if hasattr(row, k):
            setattr(row, k, v)
    try:
        async with sessions() as s:
            s.add(row)
            await s.commit()
        return row_id
    except Exception as exc:
        logger.warning("planner trace record failed: %s", exc)
        return None


async def rate(row_id: str, rating: str, note: str = "") -> bool:
    sessions = await _ensure()
    if sessions is None:
        return False
    async with sessions() as s:
        row = await s.get(TurnRow, row_id)
        if row is None:
            return False
        row.rating, row.rating_note = rating, note
        await s.commit()
        return True


def _row_dict(r: TurnRow) -> dict[str, Any]:
    d = {c.name: getattr(r, c.name) for c in TurnRow.__table__.columns}
    d["ts"] = r.ts.isoformat() if r.ts else None
    for k in ("tools_json", "plan_json", "stages_json"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except Exception:
            pass
    return d


async def recent(limit: int = 100, compare_group: Optional[str] = None) -> list[dict[str, Any]]:
    sessions = await _ensure()
    if sessions is None:
        return []
    async with sessions() as s:
        stmt = select(TurnRow).order_by(TurnRow.ts.desc()).limit(limit)
        if compare_group:
            stmt = select(TurnRow).where(TurnRow.compare_group == compare_group).order_by(TurnRow.ts.asc())
        rows = (await s.execute(stmt)).scalars().all()
    return [_row_dict(r) for r in rows]


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1)))))
    return round(values[k], 1)


async def stats() -> dict[str, Any]:
    sessions = await _ensure()
    if sessions is None:
        return {"available": False}
    async with sessions() as s:
        rows = (await s.execute(select(TurnRow).order_by(TurnRow.ts.desc()).limit(5000))).scalars().all()
    out: dict[str, Any] = {"available": True, "arms": {}, "agreement": {}, "ratings": {}}
    for arm in ("llm", "jev"):
        sub = [r for r in rows if r.arm == arm and not r.error]
        ttft = [r.ttft_ms for r in sub if r.ttft_ms is not None]
        total = [r.total_ms for r in sub if r.total_ms is not None]
        out["arms"][arm] = {
            "turns": len(sub),
            "errors": sum(1 for r in rows if r.arm == arm and r.error),
            "ttft_p50": _pct(ttft, 50), "ttft_p95": _pct(ttft, 95),
            "total_p50": _pct(total, 50), "total_p95": _pct(total, 95),
            "escalated": sum(1 for r in sub if r.escalated),
            "jev_ms_p50": _pct([r.jev_ms for r in sub if r.jev_ms], 50),
            "model_requests_avg": round(statistics.mean([r.model_requests for r in sub if r.model_requests]), 2) if any(r.model_requests for r in sub) else None,
            "ratings": {k: sum(1 for r in sub if r.rating == k) for k in ("correct", "wrong", "better", "worse")},
        }
    # Tool-plan agreement across arms sharing a compare_group.
    groups: dict[str, dict[str, TurnRow]] = {}
    for r in rows:
        if r.compare_group and r.arm in ("llm", "jev"):
            groups.setdefault(r.compare_group, {})[r.arm] = r
    same = subset = different = 0
    for g in groups.values():
        if "llm" in g and "jev" in g:
            a = _toolset(g["llm"].tools_json)
            b = _toolset(g["jev"].tools_json)
            if a == b:
                same += 1
            elif a <= b or b <= a:
                subset += 1
            else:
                different += 1
    shadow = [r for r in rows if r.arm == "shadow"]
    out["agreement"] = {
        "pairs": same + subset + different, "same_tools": same, "subset": subset, "different": different,
        "shadow_turns": len(shadow),
        "shadow_same": sum(1 for r in shadow if r.agreement == "same"),
        "shadow_subset": sum(1 for r in shadow if r.agreement == "subset"),
        "shadow_different": sum(1 for r in shadow if r.agreement == "different"),
    }
    return out


def _toolset(tools_json: Optional[str]) -> set[str]:
    try:
        return {t["name"] for t in json.loads(tools_json or "[]")}
    except Exception:
        return set()
