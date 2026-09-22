"""Per-request switches shared by BOTH arms (legacy pydantic-ai tools and the
Jev executor). Contextvars propagate into tasks spawned by the request."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

DRY_RUN_SIDE_EFFECTS: ContextVar[bool] = ContextVar("planner_dry_run_side_effects", default=False)
DISABLED_TOOLS: ContextVar[frozenset[str]] = ContextVar("planner_disabled_tools", default=frozenset())
SEARCH_TOP_K_OVERRIDE: ContextVar[Optional[int]] = ContextVar("planner_search_top_k", default=None)
# Agent-step token accounting: the StageRecorder that outgoing chat/completions
# bodies should be counted onto (set only around the agent stream).
TOKEN_SINK: ContextVar[Optional[object]] = ContextVar("planner_token_sink", default=None)


def count_tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(text or ""))
    except Exception:
        return max(1, len(text or "") // 4)


def dry_run_message(tool: str, args: dict) -> str:
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return (
        f"[TEST MODE] {tool}({shown}) was not sent: bookings and payments are switched off in this "
        "test environment. Tell the farmer plainly that this is a test and the booking was not made; "
        "do not describe what would happen in a real situation."
    )
