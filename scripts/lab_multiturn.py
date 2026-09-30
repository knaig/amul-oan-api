"""Drive a multi-turn scenario through the lab endpoint on both arms and print
what each arm did per turn (tools, TTFT, answer head). Requires the stack from
scripts/lab_up.sh.

    python scripts/lab_multiturn.py [--mobile 9876543210] [--profile gpt41]
"""
from __future__ import annotations

import argparse
import json
import uuid

import httpx

SCENARIOS = {
    "health_call_confirm": [
        "My cow has fever and is not eating since yesterday",
        "yes please book it",
    ],
    "ai_call_pick_technician": [
        "My cow is in heat, book insemination",
        "Meena",
    ],
    "mandi_followup_place_sticky": [
        "onion price in Junagadh",
        "and cotton?",
    ],
    "milk_then_bonus": [
        "show my milk for last week",
        "and what about my bonus",
    ],
}


def run(base: str, req: dict) -> dict:
    out: dict = {}
    with httpx.stream("POST", f"{base}/api/lab/turn", json=req, timeout=300) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            e = json.loads(line[6:])
            if e.get("type") == "done":
                out[e["arm"]] = e
            elif e.get("type") == "error":
                out[e["arm"]] = {"error": e["error"], "stages": e.get("stages", {}), "tools": [], "answer": ""}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--mobile", default="9876543210")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    for name, turns in SCENARIOS.items():
        if args.only and args.only != name:
            continue
        session = f"mt-{name}-{uuid.uuid4().hex[:6]}"
        print(f"\n### {name}  (session {session})")
        for turn in turns:
            print(f"\n> {turn}")
            res = run(args.base, {"query": turn, "session_id": session, "source_lang": args.lang, "target_lang": args.lang,
                                  "mobile": args.mobile, "arms": ["llm", "jev"], "dry_run_side_effects": False, "model_profile": args.profile})
            for arm in ("llm", "jev"):
                d = res.get(arm) or {}
                if d.get("error"):
                    print(f"  [{arm}] ERROR {d['error']}")
                    continue
                m = d["stages"]["marks"]
                ttft = m.get("first_client_token", 0) - m.get("agent_start", 0)
                tools = [(t["name"], {k: v for k, v in (t.get("args") or {}).items() if k not in ("remark",)}) for t in d["tools"]]
                print(f"  [{arm}] ttft={ttft:.0f}ms total={d['stages']['total_ms']:.0f}ms req={d['stages']['meta'].get('model_requests')} tools={tools}")
                print(f"        {d['answer'][:220].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
