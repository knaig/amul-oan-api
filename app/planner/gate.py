"""Hold an agent token stream until a concurrently running moderation verdict lands.

The agent stream starts immediately (overlapping the moderation request); its
chunks are buffered. When the verdict arrives: allowed -> flush and pass through;
rejected -> the agent stream is closed and ``ModerationRejected`` is raised so the
caller yields the decline text. Nothing farmer-visible is emitted before the verdict.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator


class ModerationRejected(Exception):
    def __init__(self, moderation: Any):
        super().__init__("moderation rejected the query")
        self.moderation = moderation


async def gated(source: AsyncIterator[str], verdict_task: "asyncio.Task[Any]", *, is_rejected) -> AsyncIterator[str]:
    agen = source.__aiter__()
    buffer: list[str] = []
    pending: asyncio.Future | None = asyncio.ensure_future(agen.__anext__())
    try:
        while not verdict_task.done():
            waiters = {verdict_task} | ({pending} if pending is not None else set())
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if pending is not None and pending in done:
                try:
                    buffer.append(pending.result())
                    pending = asyncio.ensure_future(agen.__anext__())
                except StopAsyncIteration:
                    pending = None
                    if not verdict_task.done():
                        await verdict_task
        moderation = verdict_task.result()  # raises if moderation itself failed
        if is_rejected(moderation):
            if pending is not None:
                pending.cancel()
                try:
                    await pending
                except BaseException:
                    pass
            await agen.aclose()
            raise ModerationRejected(moderation)
        for chunk in buffer:
            yield chunk
        if pending is not None:
            try:
                yield await pending
            except StopAsyncIteration:
                return
        async for chunk in agen:
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
