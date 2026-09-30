"""Summarise one or more planner_eval.py result files side by side.

    python scripts/planner_report.py .lab/eval_pruned_gpt41.jsonl [.lab/eval_mini.jsonl ...]

Prints per-arm TTFT / total p50+p95, model requests, tool-set accuracy vs
expected_tools, LLM-vs-Jev agreement, and a per-query table.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, min(len(values) - 1, int(round(p / 100 * (len(values) - 1)))))]


def fmt(v):
    return "-" if v is None else f"{v:.0f}"


def summarise(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    arms = [a for a in ("llm", "jev") if all(a in r for r in rows)]
    print(f"\n== {path.name}  ({len(rows)} queries)")
    print(f"{'arm':<5}{'n':>3}{'ttft p50':>10}{'ttft p95':>10}{'total p50':>11}{'total p95':>11}{'req avg':>9}{'tool acc':>10}{'escalated':>11}{'errors':>8}")
    for a in arms:
        ok = [r[a] for r in rows if not r[a]["error"]]
        ttft = [x["ttft_ms"] for x in ok if x["ttft_ms"] is not None]
        total = [x["total_ms"] for x in ok]
        req = [x["model_requests"] for x in ok if x["model_requests"]]
        exp = [r for r in rows if r.get("expected_tools") is not None and not r[a]["error"]]
        acc = sum(1 for r in exp if set(r[a]["tools"]) == set(r["expected_tools"])) / len(exp) if exp else None
        print(f"{a:<5}{len(ok):>3}{fmt(pct(ttft,50)):>10}{fmt(pct(ttft,95)):>10}{fmt(pct(total,50)):>11}{fmt(pct(total,95)):>11}"
              f"{(f'{statistics.mean(req):.2f}' if req else '-'):>9}{(f'{acc:.2f}' if acc is not None else '-'):>10}{sum(x['escalated'] for x in ok):>11}{sum(1 for r in rows if r[a]['error']):>8}")
    # List prices, USD per 1M tokens (2026-09): edit as needed.
    PRICES = {"gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60), "gemini-2.5-flash": (0.30, 2.50), "openai/gpt-4.1-mini": (0.40, 1.60)}
    JEV_IN = 0.042
    for a in arms:
        ok = [r[a] for r in rows if not r[a]["error"] and r[a].get("gen_input_tokens")]
        if ok:
            gi = statistics.mean(x["gen_input_tokens"] for x in ok); go = statistics.mean(x["gen_output_tokens"] or 0 for x in ok)
            ji = statistics.mean(x.get("jev_input_tokens") or 0 for x in ok)
            model = (ok[0].get("agent_model") or "").split(":")[-1]
            pin, pout = PRICES.get(model, (None, None))
            cost = f"   est. agent-step cost/turn ${(gi * pin + go * pout + ji * JEV_IN) / 1e6:.4f}" if pin else ""
            print(f"{a:<5}avg agent-step tokens/turn (tiktoken est.): generative in {gi:.0f} out {go:.0f}" + (f"   + Jev in {ji:.0f}" if ji else "") + f"   model {model}{cost}")
    if len(arms) == 2:
        pairs = [r for r in rows if not r["llm"]["error"] and not r["jev"]["error"]]
        same = sum(1 for r in pairs if set(r["llm"]["tools"]) == set(r["jev"]["tools"]))
        deltas = [(r["llm"]["ttft_ms"] - r["jev"]["ttft_ms"]) for r in pairs if r["llm"]["ttft_ms"] and r["jev"]["ttft_ms"]]
        print(f"tool-plan agreement: {same}/{len(pairs)}   agent-step TTFT saved by Jev: median {fmt(statistics.median(deltas)) if deltas else '-'} ms, mean {fmt(statistics.mean(deltas)) if deltas else '-'} ms")
    print(f"\n{'query':<34}{'exp':<22}{'llm tools':<34}{'llm ttft':>9}{'jev tools':<34}{'jev ttft':>9}{'esc':>4}")
    for r in rows:
        q = r["query"][:32]
        exp = ",".join(r.get("expected_tools") or []) or "-"
        def tools(a):
            names = r[a]["tools"]
            return (",".join(sorted(set(names))) + (f" x{len(names)}" if len(names) != len(set(names)) else "")) or "-"
        print(f"{q:<34}{exp[:20]:<22}{tools('llm')[:32]:<34}{fmt(r['llm']['ttft_ms']):>9}{tools('jev')[:32]:<34}{fmt(r['jev']['ttft_ms']):>9}{('Y' if r['jev']['escalated'] else ''):>4}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summarise(Path(p))
