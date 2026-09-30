"""Beckn network + PashuGPT stand-in for the planner lab.

Sits strictly BEHIND the app's network boundary, speaking the same wire
contracts the production seeker, ONIX transaction bridge and PashuGPT expose:

  POST /seeker/search                  -> {results: {leg: on_search}, errors: {}}
  POST /bridge/{search|init|confirm|status}  -> Beckn ACK, then an async on_* callback
                                          to the app's /api/beckn/on_* with the
                                          callback token (durable-operation flow)
  GET  /pashugpt/GetFarmerBonusAmount  -> PashuGPT bonus rows

Every hop has a configurable latency (env STANDIN_LAT_*_MS or POST /standin/latency)
so end-to-end turn timings stay realistic. Nothing in the app is mocked: the
planner, tools, cache, correlation store and callback ingress run unchanged.

Run:  uvicorn scripts.beckn_standin:app --port 3100
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standin_data as D  # noqa: E402

app = FastAPI(title="Beckn / PashuGPT stand-in")

LAT = {
    "vet": int(os.getenv("STANDIN_LAT_VET_MS", "900")),
    "schemes": int(os.getenv("STANDIN_LAT_SCHEMES_MS", "900")),
    "vistaar": int(os.getenv("STANDIN_LAT_VISTAAR_MS", "2200")),
    "bridge_ack": int(os.getenv("STANDIN_LAT_BRIDGE_ACK_MS", "150")),
    "callback": int(os.getenv("STANDIN_LAT_CALLBACK_MS", "1200")),
    "pashugpt": int(os.getenv("STANDIN_LAT_PASHUGPT_MS", "600")),
}
CALLBACK_URL = os.getenv("STANDIN_CALLBACK_URL", "http://127.0.0.1:8000/api/beckn").rstrip("/")
CALLBACK_TOKEN = os.getenv("STANDIN_CALLBACK_TOKEN", "lab-callback-token")
BRIDGE_TOKEN = os.getenv("STANDIN_BRIDGE_TOKEN", "lab-bridge-token")
LOG: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _sleep(key: str) -> None:
    await asyncio.sleep(LAT.get(key, 0) / 1000.0)


def _tag(code: str, value: Any = None, entries: list | None = None) -> dict:
    t: dict[str, Any] = {"descriptor": {"code": code}}
    if value is not None:
        t["value"] = str(value)
    if entries is not None:
        t["list"] = entries
    return t


def _log(kind: str, **fields: Any) -> None:
    LOG.append({"ts": _now(), "kind": kind, **fields})
    del LOG[:-500]


# ── seeker (discovery) ───────────────────────────────────────────────────────

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "in", "for", "to", "my", "is", "are", "with", "on", "at", "cattle", "cow", "cows", "buffalo", "dairy", "animal"}


def _score(query: str, doc: dict) -> float:
    q = [t for t in _TOKEN.findall(query.lower()) if t not in _STOP and len(t) > 2]
    if not q:
        return 0.0
    text = (doc["name"] + " " + doc["text"]).lower()
    hits = sum(1 for t in q if t in text) + 0.5 * sum(1 for t in q if t[:5] in text)
    title_hits = sum(2 for t in q if t in doc["name"].lower())
    return (hits + title_hits) / (len(q) + 1)


def _vet_items(query: str) -> list[dict]:
    scored = sorted(((_score(query, d), d) for d in D.VET_KB), key=lambda x: -x[0])
    return [
        {"id": f"vetkb-{i}", "descriptor": {"name": d["name"], "long_desc": d["text"]}, "tags": [_tag("source", d["source"]), _tag("score", round(s, 3))]}
        for i, (s, d) in enumerate(scored) if s > 0
    ][:12]


def _scheme_items(query: str, union: str | None) -> list[dict]:
    unions = [union] if union and union in D.UNION_SCHEMES else list(D.UNION_SCHEMES)
    q = query.lower()
    out = []
    for u in unions:
        for s in D.UNION_SCHEMES[u]:
            relevant = q in ("schemes", "") or any(w in (s["title"] + " " + s["desc"]).lower() for w in _TOKEN.findall(q) if len(w) > 3)
            if relevant or q == "schemes":
                out.append({"id": f"{u}-{s['title'][:20]}", "descriptor": {"name": s["title"], "long_desc": s["desc"]},
                            "tags": [_tag("union", u), _tag("category", s["category"]), _tag("source", s["source"])]})
    if not out:  # title matching missed: return the union's full list like the real leg does for generic words
        for u in unions:
            out.extend({"id": f"{u}-{s['title'][:20]}", "descriptor": {"name": s["title"], "long_desc": s["desc"]}, "tags": [_tag("union", u), _tag("category", s["category"]), _tag("source", s["source"])]} for s in D.UNION_SCHEMES[u])
    return out


def _flat(code: str, value: Any) -> dict:
    return {"descriptor": {"code": code}, "value": str(value)}


def _vistaar_items(intent: dict, query: str) -> list[dict]:
    cat = ((intent.get("category") or {}).get("descriptor") or {})
    code = (cat.get("code") or cat.get("name") or "").lower()
    if code == "price-discovery":
        commodity = ((intent.get("item") or {}).get("descriptor") or {}).get("name") or query
        town = (((intent.get("fulfillment") or {}).get("end") or {}).get("location") or {}).get("descriptor", {}).get("name") or "anand"
        tags = {t.get("code"): t.get("value") for t in intent.get("tags", []) if isinstance(t, dict)}
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        def _p(v, d):
            try:
                return datetime.strptime(v, "%d-%m-%Y").date()
            except Exception:
                return d
        to_d = _p(tags.get("to_date", ""), today)
        from_d = _p(tags.get("from_date", ""), to_d - timedelta(days=7))
        rows = D.mandi_rows(commodity, town, from_d, to_d)
        return [{
            "id": f"price-{i}", "descriptor": {"name": r["name"]},
            "tags": [{"descriptor": {"code": "price"}, "list": [
                _flat("Market", r["market"]), _flat("District", r["district"]), _flat("State", r["state"]), _flat("Arrival Date", r["date"]),
                _flat("Modal Price", r["modal"]), _flat("Min Price", r["min"]), _flat("Max Price", r["max"]), _flat("Price Unit", "Rs./Qtl"),
                _flat("Variety", r["variety"]), _flat("Grade", r["grade"])]}],
        } for i, r in enumerate(rows)]
    if code in ("wfc", "weather-forecast-mausamgram"):
        today = date.today()
        items = []
        for i, (sky, rain, tmin, tmax, hum, wind) in enumerate(D.WEATHER_TEMPLATE):
            d = today + timedelta(days=i)
            items.append({"id": f"wx-{i}", "descriptor": {"name": f"Forecast {d.strftime('%d %b')}", "short_desc": sky},
                          "tags": [{"descriptor": {"code": "forecast"}, "list": [_flat("Date", d.isoformat()), _flat("Sky", sky), _flat("Rainfall (mm)", rain), _flat("Min Temp (C)", tmin), _flat("Max Temp (C)", tmax), _flat("Humidity (%)", hum), _flat("Wind (km/h)", wind)]}]})
        return items
    if code == "schemes-agri":
        scheme = ((intent.get("item") or {}).get("descriptor") or {}).get("name") or query
        s = D.CENTRAL_SCHEMES.get(scheme.lower().strip())
        if not s:
            return []
        return [{"id": f"scheme-{scheme}", "descriptor": {"name": s["name"], "long_desc": s["desc"]}, "tags": [_tag("category", s["category"]), _tag("source", "vistaar.da.gov.in")]}]
    return []


@app.post("/seeker/search")
async def seeker_search(body: dict):
    query = str(body.get("query") or "")
    legs = body.get("legs") or []
    intent = body.get("intent") or {}
    results: dict[str, Any] = {}
    for leg in legs:
        if leg == "amulvet":
            await _sleep("vet")
            items = _vet_items(query)
        elif leg == "amulschemes":
            await _sleep("schemes")
            union = None
            for t in intent.get("tags", []):
                for e in (t.get("list") or []):
                    if (e.get("descriptor") or {}).get("code") == "union":
                        union = e.get("value")
            items = _scheme_items(query, union)
        elif leg == "vistaar":
            await _sleep("vistaar")
            items = _vistaar_items(intent, query)
        else:
            results[leg] = None
            continue
        results[leg] = {"context": {"action": "on_search", "domain": leg}, "message": {"catalog": {"providers": [{"id": f"{leg}-provider", "items": items}]}}}
    _log("seeker", legs=legs, query=query[:80], counts={k: len(((v or {}).get("message") or {}).get("catalog", {}).get("providers", [{}])[0].get("items", [])) for k, v in results.items() if v})
    return {"results": results, "errors": {}, "traces": {}}


# ── ONIX transaction bridge (durable action -> callback) ─────────────────────

def _accounts_for(mobile: str) -> list[dict]:
    digits = re.sub(r"\D", "", mobile)[-10:]
    return D.FARMERS.get(digits, [])


def _farmer_profile_callback(order: dict) -> dict:
    phone = (((order.get("fulfillments") or [{}])[0].get("customer") or {}).get("contact") or {}).get("phone", "")
    accounts = _accounts_for(phone)
    if not accounts:
        return {"order": {"state": "NOT_FOUND"}}
    groups = []
    for a in accounts:
        entries = [_tag(k, v) for k, v in a.items() if k not in ("tags", "technicians") and v not in (None, "")]
        entries += [_tag("tag_id", t) for t in a.get("tags", [])]
        groups.append(_tag("farmer_accounts", entries=entries))
    return {"order": {"state": "COMPLETED", "fulfillments": [{"customer": {"person": {"tags": groups}}}]}}


def _animal_callback(order: dict) -> dict:
    provider = (order.get("provider") or {}).get("id")
    item = (order.get("items") or [{}])[0]
    tag_id = next((t.get("value") for t in item.get("tags", []) if (t.get("descriptor") or {}).get("code") == "tag_id"), "")
    animal = D.ANIMALS.get(tag_id)
    if animal is None:
        return {"order": {"state": "NOT_FOUND"}}
    if provider == "amulpashudhan":
        tags = [_tag("animal_profile", entries=[_tag("tag_id", tag_id)] + [_tag(k, v) for k, v in animal.items()])]
    elif provider == "amuldairy":
        tags = [_tag("animal_profile", entries=[_tag("tag_id", tag_id), _tag("animal_type", animal["animal_type"]), _tag("breed", animal["breed"]), _tag("milking_stage", animal["milking_stage"]), _tag("lactation_number", animal["lactation_number"]), _tag("milk_yield", "9.5")]),
                _tag("vaccination", entries=[_tag("vaccine_name", "FMD Raksha"), _tag("vaccination_type", "Routine"), _tag("vaccination_date", "2026-03-10"), _tag("disease", "FMD")]),
                _tag("deworming", entries=[_tag("deworming_date", "2026-06-02"), _tag("dewormer_name", "Albendazole"), _tag("dewormer_dose", "3 g")])]
    else:  # banasmobileapi operated visits
        tags = []
        for i, v in enumerate(D.BANAS_VISITS.get(tag_id, [])):
            entries = [_tag(k, val) for k, val in v.items() if k != "medicines"] + [_tag("visit_index", i)]
            tags.append(_tag("operated_visit", entries=entries))
            for m in v.get("medicines", []):
                tags.append(_tag("visit_medicine", entries=[_tag("visit_index", i)] + [_tag(k, val) for k, val in m.items()]))
        if not tags:
            return {"order": {"state": "NOT_FOUND"}}
    return {"order": {"state": "COMPLETED", "items": [{"id": item.get("id"), "tags": tags}]}}


def _milk_callback(order: dict) -> dict:
    f = (order.get("fulfillments") or [{}])[0]
    details = {}
    for g in f.get("tags", []):
        for e in g.get("list", []):
            details[(e.get("descriptor") or {}).get("code")] = e.get("value")
    start = date.fromisoformat(details["fromdate"])
    end = date.fromisoformat(details["todate"])
    milk, ded = D.milk_rows(details.get("farmer_code", "0"), start, end)
    tags = [_tag("query-period", entries=[_tag("result", "success"), _tag("fromdate", start.isoformat()), _tag("todate", end.isoformat())])]
    tags += [_tag("milk-record", entries=[_tag(k, v) for k, v in r.items()]) for r in milk]
    tags += [_tag("deduction-record", entries=[_tag(k, v) for k, v in r.items()]) for r in ded]
    return {"order": {"state": "COMPLETED", "items": [{"id": "milk-collection-details", "tags": tags}]}}


def _technician_catalog(intent: dict) -> dict:
    stops = ((intent.get("fulfillment") or {}).get("stops") or [{}])
    society = (((stops[0].get("location") or {}).get("descriptor") or {}).get("code") or "").replace("society:", "")
    techs = []
    for accounts in D.FARMERS.values():
        for a in accounts:
            if a["society_code"] == society:
                techs = a.get("technicians", [])
    items = [{"id": f"ait:{t['id']}", "descriptor": {"name": f"{t['name']} (AI technician)"},
              "tags": [_tag("technician-details", entries=[_tag("technician_id", t["id"]), _tag("mobile", t["mobile"]), _tag("gujarati_full_name", t.get("gu", ""))])]} for t in techs]
    return {"catalog": {"providers": [{"id": "amul-ai-service", "items": items}]}}


def _confirm_callback(order: dict) -> dict:
    item_id = (order.get("items") or [{}])[0].get("id", "")
    prefix = "AI" if item_id.startswith("ait:") else "HC"
    ticket = f"{prefix}-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:5].upper()}"
    return {"order": {"id": ticket, "status": "ACTIVE", "items": order.get("items"), "fulfillments": order.get("fulfillments")}}


def _shc_callback(order: dict) -> dict | None:
    f = (order.get("fulfillments") or [{}])[0]
    phone = ((f.get("customer") or {}).get("contact") or {}).get("phone", "")
    cycle = next((t.get("value") for t in (((f.get("customer") or {}).get("person") or {}).get("tags") or []) if (t.get("descriptor") or {}).get("code") == "cycle"), "")
    html = D.SHC_CYCLES.get(re.sub(r"\D", "", phone)[-10:], {}).get(cycle)
    if not html:
        return None  # BV emits no on_init for a missing card; the app treats the wait as NO_CARD
    encoded = base64.b64encode(html.encode()).decode()
    return {"order": {"providers": [{"id": "shc-discovery", "items": [{"id": "soil-health-card", "media": [{"mimetype": "text/html", "url": f"data:text/html;base64,{encoded}"}]}]}]}}


async def _deliver(context: dict, action: str, message: dict | None, error: dict | None = None) -> None:
    await _sleep("callback")
    cb_action = f"on_{action}"
    payload: dict[str, Any] = {"context": {**{k: context.get(k) for k in ("domain", "version", "bap_id", "bap_uri", "bpp_id", "bpp_uri", "transaction_id", "message_id", "location")}, "action": cb_action, "timestamp": _now()}}
    if message is not None:
        payload["message"] = message
    if error is not None:
        payload["error"] = error
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{CALLBACK_URL}/{cb_action}", json=payload, headers={"X-Beckn-Callback-Token": CALLBACK_TOKEN})
            _log("callback", action=cb_action, domain=context.get("domain"), status=r.status_code, ack=(r.json().get("message") or {}).get("ack") if r.headers.get("content-type", "").startswith("application/json") else None)
    except Exception as exc:
        _log("callback_error", action=cb_action, error=repr(exc))


@app.post("/bridge/{action}")
async def bridge(action: str, body: dict, authorization: str | None = Header(default=None)):
    context = body.get("context") or {}
    domain = context.get("domain", "")
    if domain != "schemes:vistaar" and authorization != f"Bearer {BRIDGE_TOKEN}":
        raise HTTPException(status_code=401, detail="bad bridge token")
    message = body.get("message") or {}
    order = message.get("order") or {}
    intent = message.get("intent") or {}
    await _sleep("bridge_ack")
    cb: dict | None
    err: dict | None = None
    if action == "init" and domain == "data:amul-farmer-profile":
        cb = _farmer_profile_callback(order)
    elif action == "init" and domain == "data:amul-animal-profile":
        cb = _animal_callback(order)
    elif action == "init" and domain == "services:amul-milk-collection":
        cb = _milk_callback(order)
    elif action == "init" and domain == "schemes:vistaar":
        cb = _shc_callback(order)
        if cb is None:
            _log("bridge", action=action, domain=domain, note="no card for cycle -> no on_init (BV behaviour)")
            return {"message": {"ack": {"status": "ACK"}}}
    elif action == "search":
        cb = _technician_catalog(intent)
    elif action == "confirm":
        cb = _confirm_callback(order)
    elif action == "status":
        cb = {"order": {"id": message.get("order_id"), "status": "ACTIVE"}}
    else:
        return JSONResponse(status_code=200, content={"message": {"ack": {"status": "NACK"}}, "error": {"code": "UNSUPPORTED", "message": f"unsupported {action} for {domain}"}})
    _log("bridge", action=action, domain=domain, provider=(order.get("provider") or intent.get("provider") or {}).get("id"))
    asyncio.create_task(_deliver(context, action, cb, err))
    return {"message": {"ack": {"status": "ACK"}}}


# ── PashuGPT bonus (direct provider API) ─────────────────────────────────────

@app.get("/pashugpt/GetFarmerBonusAmount")
async def bonus(unionCode: str, societyCode: str, farmerCode: str, authorization: str | None = Header(default=None)):
    await _sleep("pashugpt")
    rows = D.BONUS.get((unionCode, societyCode, farmerCode))
    _log("pashugpt", union=unionCode, society=societyCode, farmer=farmerCode, found=bool(rows))
    if rows is None:
        return JSONResponse(status_code=400, content="Farmer bonus data not found.")
    return rows


# ── operator endpoints ───────────────────────────────────────────────────────

@app.get("/standin/config")
async def config():
    return {"latency_ms": LAT, "callback_url": CALLBACK_URL, "farmers": list(D.FARMERS), "vet_docs": len(D.VET_KB), "log_tail": LOG[-30:]}


@app.post("/standin/latency")
async def set_latency(body: dict):
    for k, v in body.items():
        if k in LAT:
            LAT[k] = int(v)
    return {"latency_ms": LAT}
