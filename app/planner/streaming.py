"""Pipelined answer translation.

The sequential path pulls one English chunk at a time and translates each
finished batch before pulling the next, so the generating model idles while a
translation call runs and translation calls run one after another. Here:

  english_src --drain--> chunk queue --cut (same sentence rules)--> batch queue
  batch queue --filler--> up to `max_in_flight` translation tasks, each streaming
                         into its own chunk queue
  emitter: yields the head batch's chunks as they arrive, then the next, in order.

First-word latency is unchanged (the first batch streams live); later batches
are usually finished by the time their turn comes.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import AsyncIterator, Callable

_END = object()


async def pipelined_translate(
    english_src: AsyncIterator[str],
    *,
    cut_batches,                      # (async chunk iterator) -> async iterator of batch strings
    translate: Callable[[str], AsyncIterator[str]],
    max_in_flight: int = 3,
) -> AsyncIterator[str]:
    chunk_q: asyncio.Queue = asyncio.Queue()
    pending: deque[tuple[asyncio.Queue, asyncio.Task]] = deque()
    cond = asyncio.Condition()
    state = {"cutting_done": False}

    async def _drain() -> None:
        try:
            async for chunk in english_src:
                await chunk_q.put(chunk)
        finally:
            await chunk_q.put(_END)

    async def _from_queue():
        while True:
            item = await chunk_q.get()
            if item is _END:
                return
            yield item

    async def _translate_into(batch: str, out_q: asyncio.Queue) -> None:
        try:
            async for t in translate(batch):
                await out_q.put(t)
        finally:
            await out_q.put(_END)

    async def _cut_and_fill() -> None:
        try:
            async for batch in cut_batches(_from_queue()):
                async with cond:
                    while len(pending) >= max_in_flight:
                        await cond.wait()
                    out_q: asyncio.Queue = asyncio.Queue()
                    pending.append((out_q, asyncio.create_task(_translate_into(batch, out_q))))
                    cond.notify_all()
        finally:
            async with cond:
                state["cutting_done"] = True
                cond.notify_all()

    drain_task = asyncio.create_task(_drain())
    fill_task = asyncio.create_task(_cut_and_fill())
    try:
        while True:
            async with cond:
                while not pending and not state["cutting_done"]:
                    await cond.wait()
                if not pending:
                    break
                out_q, task = pending[0]
            while True:
                item = await out_q.get()
                if item is _END:
                    break
                yield item
            await task
            async with cond:
                pending.popleft()
                cond.notify_all()
    finally:
        for _, t in pending:
            if not t.done():
                t.cancel()
        for t in (fill_task, drain_task):
            if not t.done():
                t.cancel()
        for t in (fill_task, drain_task, *[t for _, t in pending]):
            try:
                await t
            except BaseException:
                pass
