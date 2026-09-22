"""Thin async wrapper over the TypeSafe SDK with timing + graceful absence."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from helpers.utils import get_logger
from app.planner.config import typesafe_api_key

logger = get_logger(__name__)

try:
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
except ImportError:  # pragma: no cover
    AsyncTypeSafeClient = None  # type: ignore[assignment]
    RetryPolicy = None  # type: ignore[assignment]


class JevUnavailable(RuntimeError):
    """No key / SDK missing / API failure. The caller escalates to the LLM planner."""


@dataclass
class JevResult:
    answers: dict[str, Any]     # question id -> plain dict (type, choice/noul/score, probabilities, confidence)
    model: str
    input_tokens: int
    output_tokens: int
    ms: float
    request_id: Optional[str]


_client: Any = None


def _get_client(timeout_s: float) -> Any:
    global _client
    if AsyncTypeSafeClient is None:
        raise JevUnavailable("typesafe-sdk is not installed")
    key = typesafe_api_key()
    if not key:
        raise JevUnavailable("TYPESAFE_API_KEY is not set")
    if _client is None:
        import httpx
        # httpx drops idle keep-alive connections after 5 s by default; a turn
        # arrives less often than that, so every plan paid a fresh TLS handshake
        # (~0.9 s from India). Keep the connection for a long time instead.
        http_client = httpx.AsyncClient(
            timeout=timeout_s,
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=32, keepalive_expiry=600.0),
        )
        _client = AsyncTypeSafeClient(
            api_key=key,
            timeout=timeout_s,
            retry=RetryPolicy(max_retries=2) if RetryPolicy else None,
            http_client=http_client,
        )
    return _client


async def keepalive_loop(interval_s: float = 25.0, *, model: str = "jev-latest") -> None:
    """Keep the TypeSafe connection (and the server side) warm with a tiny request.

    One noul over a one-line state: ~40 input tokens, i.e. about $0.000002 per ping.
    Runs only when a key is configured; any failure is logged and retried next tick."""
    import asyncio

    while True:
        try:
            if available():
                await evaluate("ping", {"ok": {"type": "noul", "instructions": "Is this the word ping?"}}, model=model, timeout_s=5.0)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("jev keepalive ping failed: %s", exc)
        await asyncio.sleep(interval_s)


def _plain(answer: Any) -> dict[str, Any]:
    if hasattr(answer, "model_dump"):
        return answer.model_dump()
    return dict(answer)


async def evaluate(state: Any, questions: dict[str, Any], *, model: str, timeout_s: float) -> JevResult:
    """One System One request. Raises JevUnavailable on any failure."""
    client = _get_client(timeout_s)
    t0 = time.monotonic()
    try:
        response = await client.system_one(state, questions, model=model, timeout=timeout_s)
    except Exception as exc:
        logger.warning("jev evaluate failed: %s: %s", type(exc).__name__, exc)
        raise JevUnavailable(f"{type(exc).__name__}: {exc}") from exc
    ms = (time.monotonic() - t0) * 1000.0
    usage = getattr(response, "usage", None)
    return JevResult(
        answers={k: _plain(v) for k, v in response.answers.items()},
        model=getattr(response, "model", model),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        ms=ms,
        request_id=getattr(response, "request_id", None),
    )


def available() -> bool:
    return AsyncTypeSafeClient is not None and bool(typesafe_api_key())
