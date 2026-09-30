"""Batch side-by-side evaluation of the two agent-step arms.

Usage:
    python scripts/planner_eval.py queries.jsonl [--arms llm,jev] [--mobile 98xxxxxxxx]
                                   [--source-lang gu --target-lang gu] [--no-dry-run]

queries.jsonl: one JSON object per line: {"query": "...", "expected_tools": ["search_documents"]}
(expected_tools optional). Each query runs on BOTH arms with fresh sessions, side effects
dry-run by default, and prints per-arm TTFT / total / tools plus agreement with the
expected tool set. Rows are persisted to the planner trace store like lab turns.

Needs the same .env as the app (OPENAI_API_KEY, TYPESAFE_API_KEY, BECKN_*, REDIS_*).
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import statistics
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import BackgroundTasks  # noqa: E402


async def run_arm(arm: str, item: dict, args, group: str) -> dict:
    from app.planner.models import StageRecorder
    from app.planner.side_effects import DRY_RUN_SIDE_EFFECTS
    from app.services.chat import stream_chat_messages
    from agents.tools.farmer import normalize_phone_to_mobile

    DRY_RUN_SIDE_EFFECTS.set(not args.no_dry_run)
    stages, sink = StageRecorder(), {}
    mobile = item.get("mobile") or args.mobile
    phone = normalize_phone_to_mobile(mobile) if mobile else None
    session = f"eval-{group}::{arm}"
    out = []
    err = None
    try:
        async for chunk in stream_chat_messages(
            query=item["query"], session_id=session, source_lang=args.source_lang, target_lang=args.target_lang,
            channel="web", user_id=phone or "eval", history=[], user_info={"phone": phone, "sub": phone} if phone else {},
            background_tasks=BackgroundTasks(), planner=arm, stages=stages, turn_sink=sink, compare_group=group,
            planner_overrides={"dry_run_side_effects": not args.no_dry_run, "concurrent_moderation": args.concurrent_moderation}, emit_artifact_frames=False,
            model_profile=args.profile,
        ):
            out.append(chunk)
    except Exception as exc:  # keep going; the failure is a data point
        err = f"{type(exc).__name__}: {exc}"
    m = stages.marks
    return {
        "arm": arm, "answer": "".join(out), "error": err,
        "tools": [t["name"] for t in sink.get("tools", [])], "tool_args": [t.get("args") for t in sink.get("tools", [])],
        "ttft_ms": (m.get("first_client_token", 0) - m.get("agent_start", 0)) if "first_client_token" in m else None,
        "total_ms": stages.elapsed_ms(), "model_requests": stages.meta.get("model_requests"),
        "escalated": bool(stages.meta.get("escalated")), "jev_ms": stages.meta.get("jev_ms"),
        "gen_input_tokens": stages.meta.get("gen_input_tokens_est") or stages.meta.get("gen_input_tokens"),
        "gen_output_tokens": stages.meta.get("gen_output_tokens_est") or stages.meta.get("gen_output_tokens"),
        "jev_input_tokens": stages.meta.get("jev_input_tokens"), "agent_model": stages.meta.get("agent_model"),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("queries")
    ap.add_argument("--arms", default="llm,jev")
    ap.add_argument("--mobile", default=None)
    ap.add_argument("--source-lang", default="gu")
    ap.add_argument("--target-lang", default="gu")
    ap.add_argument("--no-dry-run", action="store_true", help="execute bookings / loan codes for real")
    ap.add_argument("--out", default=None, help="write per-query JSONL results here")
    ap.add_argument("--profile", default=None, help="pipeline profile (model set) for both arms, e.g. gpt41 | gpt41mini | gemini_flash")
    ap.add_argument("--concurrent-moderation", action="store_true", help="voice-style: moderation overlaps the agent step on both arms")
    args = ap.parse_args()

    from app.llm_core import runtime
    runtime.configure()
    from helpers.utils import load_prompt_templates
    from app.config import settings
    load_prompt_templates(settings.base_dir / "assets" / "prompts")

    items = [json.loads(line) for line in Path(args.queries).read_text(encoding="utf-8").splitlines() if line.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    results = []
    for item in items:
        group = uuid.uuid4().hex[:10]
        tasks = [asyncio.create_task(run_arm(arm, item, args, group), context=contextvars.copy_context()) for arm in arms]
        per_arm = {r["arm"]: r for r in await asyncio.gather(*tasks)}
        row = {"query": item["query"], "expected_tools": item.get("expected_tools"), **{a: per_arm[a] for a in arms}}
        results.append(row)
        line = f"{item['query'][:48]:<50}"
        for a in arms:
            r = per_arm[a]
            hit = "" if not item.get("expected_tools") else (" ✓" if set(r["tools"]) == set(item["expected_tools"]) else " ✗")
            line += f" | {a}: ttft {r['ttft_ms'] and round(r['ttft_ms'])!s:>5} total {round(r['total_ms']):>6} tools {','.join(r['tools']) or '-'}{hit}{' ESC' if r['escalated'] else ''}{' ERR' if r['error'] else ''}"
        print(line)

    print("\nSummary")
    for a in arms:
        rs = [r[a] for r in results if not r[a]["error"]]
        ttft = [r["ttft_ms"] for r in rs if r["ttft_ms"] is not None]
        total = [r["total_ms"] for r in rs]
        exp = [r for r in results if r["expected_tools"] is not None and not r[a]["error"]]
        acc = sum(1 for r in exp if set(r[a]["tools"]) == set(r["expected_tools"])) / len(exp) if exp else None
        print(f"  {a:>4}: n={len(rs)} ttft p50={statistics.median(ttft) if ttft else None:.0f} p95={sorted(ttft)[int(len(ttft)*0.95)-1] if len(ttft) > 1 else (ttft[0] if ttft else float('nan')):.0f} "
              f"total p50={statistics.median(total) if total else float('nan'):.0f} escalated={sum(r['escalated'] for r in rs)} "
              f"tool-set accuracy={acc if acc is None else round(acc, 3)} errors={sum(1 for r in results if r[a]['error'])}")
    if len(arms) == 2:
        pairs = [r for r in results if not r[arms[0]]["error"] and not r[arms[1]]["error"]]
        same = sum(1 for r in pairs if set(r[arms[0]]["tools"]) == set(r[arms[1]]["tools"]))
        print(f"  tool-plan agreement {arms[0]} vs {arms[1]}: {same}/{len(pairs)}")
    if args.out:
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in results), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
