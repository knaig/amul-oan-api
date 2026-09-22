"""Planner configuration (env-driven; self-contained so app.config stays untouched)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

from app.config import get_config_value

PlannerMode = Literal["llm", "jev", "shadow"]
LowConfidencePolicy = Literal["escalate_llm", "search_documents", "ask_clarification"]


def _bool(name: str, default: bool) -> bool:
    raw = get_config_value(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = get_config_value(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    raw = get_config_value(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass
class PlannerSettings:
    """Per-turn adjustable knobs. The lab UI sends overrides for any field."""

    mode: PlannerMode = "llm"
    typesafe_model: str = "jev-latest"
    typesafe_timeout_s: float = 8.0
    # Decision thresholds (tuned on your data; see docs/JEV_PLANNER.md).
    tool_choice_min_confidence: float = 0.45
    extra_tool_noul_threshold: float = 0.70
    arg_min_confidence: float = 0.40
    yes_threshold: float = 0.60
    low_confidence_policy: LowConfidencePolicy = "escalate_llm"
    # Retrieval shaping.
    search_top_k: int = 8
    search_fanout: int = 2  # parallel query variants (1 = passthrough only)
    milk_default_range_days: int = 7
    # Safety.
    dry_run_side_effects: bool = False
    disabled_tools: list[str] = field(default_factory=list)
    # History context sent to Jev as state (message pairs).
    history_pairs: int = 3
    # Voice-style: run the moderation request concurrently with the agent step and
    # hold farmer-visible tokens until the verdict (side-effecting tools already
    # wait on it via FarmerContext.ensure_in_scope). Applies to BOTH arms.
    concurrent_moderation: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_env(cls) -> "PlannerSettings":
        mode = str(get_config_value("PLANNER_MODE", "llm") or "llm").strip().lower()
        if mode not in ("llm", "jev", "shadow"):
            mode = "llm"
        policy = str(get_config_value("PLANNER_LOW_CONFIDENCE_POLICY", "escalate_llm") or "escalate_llm").strip()
        if policy not in ("escalate_llm", "search_documents", "ask_clarification"):
            policy = "escalate_llm"
        disabled = [t.strip() for t in str(get_config_value("PLANNER_DISABLED_TOOLS", "") or "").split(",") if t.strip()]
        return cls(
            mode=mode,  # type: ignore[arg-type]
            typesafe_model=str(get_config_value("TYPESAFE_MODEL", "jev-latest") or "jev-latest"),
            typesafe_timeout_s=_float("TYPESAFE_TIMEOUT_S", 8.0),
            tool_choice_min_confidence=_float("PLANNER_TOOL_MIN_CONFIDENCE", 0.45),
            extra_tool_noul_threshold=_float("PLANNER_EXTRA_TOOL_THRESHOLD", 0.70),
            arg_min_confidence=_float("PLANNER_ARG_MIN_CONFIDENCE", 0.40),
            yes_threshold=_float("PLANNER_YES_THRESHOLD", 0.60),
            low_confidence_policy=policy,  # type: ignore[arg-type]
            search_top_k=_int("PLANNER_SEARCH_TOP_K", 8),
            search_fanout=_int("PLANNER_SEARCH_FANOUT", 2),
            milk_default_range_days=_int("PLANNER_MILK_DEFAULT_RANGE_DAYS", 7),
            dry_run_side_effects=_bool("PLANNER_DRY_RUN_SIDE_EFFECTS", False),
            disabled_tools=disabled,
            history_pairs=_int("PLANNER_HISTORY_PAIRS", 3),
            concurrent_moderation=_bool("PLANNER_CONCURRENT_MODERATION", False),
        )

    def merged(self, overrides: Optional[dict]) -> "PlannerSettings":
        if not overrides:
            return self
        data = self.to_dict()
        for key, value in overrides.items():
            if key in data and value is not None:
                data[key] = value
        return PlannerSettings(**data)


def typesafe_api_key() -> Optional[str]:
    return get_config_value("TYPESAFE_API_KEY")


def planner_override_enabled() -> bool:
    return _bool("PLANNER_OVERRIDE_ENABLED", True)


def lab_enabled() -> bool:
    env = str(get_config_value("ENVIRONMENT", "production") or "production").lower()
    return _bool("PLANNER_LAB_ENABLED", env != "production")


def trace_db_url() -> str:
    return str(get_config_value("PLANNER_TRACE_DB_URL", "sqlite+aiosqlite:///./planner_traces.db") or "sqlite+aiosqlite:///./planner_traces.db")
