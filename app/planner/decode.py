"""Answers -> Plan. Mirrors the 'Routing Rules' of the agent prompt in code."""
from __future__ import annotations

import re
from typing import Any, Optional

from agents.deps import FarmerContext
from agents.tools.models.union import UNION_BANNED_MESSAGE
from app.planner import candidates as C
from app.planner.config import PlannerSettings
from app.planner.keywords import search_queries
from app.planner.models import Plan, ToolCall
from app.planner.questions import TurnGates

HEALTH_OFFER_LINE = "It seems your animal might need medical attention. Would you like to book a health call?"


class _Answers:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.used: list[float] = []

    def choice(self, qid: str, default: str = "") -> str:
        a = self.raw.get(qid)
        if not a:
            return default
        self.used.append(float(a.get("confidence", 1.0)))
        return str(a.get("choice", default))

    def conf(self, qid: str) -> float:
        a = self.raw.get(qid) or {}
        return float(a.get("confidence", 0.0))

    def prob(self, qid: str, option: str) -> float:
        a = self.raw.get(qid) or {}
        return float((a.get("probabilities") or {}).get(option, 0.0))

    def noul(self, qid: str) -> float:
        a = self.raw.get(qid)
        if not a:
            return 0.0
        return float(a.get("noul", 0.0))

    def yes(self, qid: str, threshold: float) -> bool:
        return self.noul(qid) >= threshold


def _fmt(d) -> str:
    return d.strftime("%d-%m-%Y")


def _pick_account(a: _Answers, gates: TurnGates) -> Optional[C.Account]:
    if not gates.accounts:
        return None
    if len(gates.accounts) == 1:
        return gates.accounts[0]
    label = a.choice("account_for_booking", "not_stated")
    for acct in gates.accounts:
        if acct.label() == label:
            return acct
    return gates.accounts[0]


def _species(a: _Answers, gates: TurnGates, qid: str = "species_for_booking") -> Optional[str]:
    picked = a.choice(qid, "not_stated")
    if picked in ("cow", "buffalo"):
        return picked
    named = a.raw.get("species_named", {}).get("choice")
    if named in ("cow", "buffalo"):
        return named
    return C.herd_species_default(gates.accounts)


def _location(a: _Answers) -> Optional[str]:
    place = a.choice("place_named", "not_stated")
    if place and place != "not_stated":
        return place
    span = a.choice("place_span", "none") if "place_span" in a.raw else "none"
    return None if span == "none" else span


def decode(deps: FarmerContext, gates: TurnGates, answers: dict[str, Any], settings: PlannerSettings) -> Plan:
    a = _Answers(answers)
    yes = settings.yes_threshold
    tools = set(gates.enabled_tools())
    calls: list[ToolCall] = []
    notes: list[str] = []
    clarification: Optional[str] = None
    query = deps.query or ""

    def add(name: str, args: dict, conf: float = 1.0, source: str = "jev") -> None:
        if name in tools and not any(c.name == name and c.args == args for c in calls):
            calls.append(ToolCall(name=name, args=args, confidence=conf, source=source))

    def search(topic: str, conf: float = 1.0) -> None:
        for qtext in search_queries(query, topic=topic, fanout=settings.search_fanout):
            add("search_documents", {"query": qtext, "top_k": settings.search_top_k}, conf)

    # ── Doctor persona: retrieval only ───────────────────────────────────────
    if gates.persona == "doctor":
        need = a.noul("needs_search")
        topic = a.choice("search_topic", "clinical")
        if need >= yes and "search_documents" in tools:
            search(topic if topic != "other" else "clinical", need)
        return Plan(intent="clinical", tool_calls=calls, confidence=min(a.used or [need]) if a.used else need,
                    answers=answers)

    intent = a.choice("intent", "clinical")
    primary = a.choice("primary_tool", "none_answer_directly")
    primary_conf = a.conf("primary_tool")
    deterministic = False  # a code rule decided the route; no confidence gate applies

    ai = a.choice("ai_request", "none") if "create_ai_call" in tools else "none"
    ai_route = ai != "none" or primary == "create_ai_call"

    # ── Health call (rule 1 of booking routing; precedence over retrieval) ──
    health = a.choice("health_request", "none") if "create_health_call" in tools else "none"
    offered = a.yes("last_assistant_offered_health_call", yes) and a.yes("farmer_says_yes", yes)
    booking_shaped_primary = primary in ("create_health_call", "none_answer_directly", "search_documents")
    health_wins = (
        (primary == "create_health_call"
         or (booking_shaped_primary and (health in ("explicit_booking_request", "confirms_earlier_offer") or offered)))
        and (not ai_route or (primary == "create_health_call" and intent == "clinical"))
    )
    if health_wins:
        deterministic = True
        if not gates.signed_in:
            notes.append("The farmer wants a veterinary health visit but is not signed in / has no profile: explain that booking needs their registered profile and suggest contacting their milk society.")
        else:
            acct = _pick_account(a, gates)
            species = _species(a, gates)
            severity = a.choice("case_severity", "normal")
            if acct is None or not (acct.union_code and acct.society_code and acct.farmer_code):
                notes.append("Health call requested but union/society/farmer codes are missing from the profile: ask the farmer for these codes (preserve leading zeros) before booking.")
            elif species is None:
                clarification = "Ask once, briefly, whether the sick animal is a cow or a buffalo so the health call can be booked."
            else:
                add("create_health_call", {
                    "union_code": acct.union_code, "society_code": acct.society_code, "farmer_code": acct.farmer_code,
                    "species": species, "case_type": severity if severity in ("normal", "emergency") else "normal",
                    "remark": query[:200],
                }, min(a.conf("health_request") or 1.0, a.conf("species_for_booking") or 1.0), "rule")
    elif health == "describes_problem_only" and not ai_route and intent in ("clinical", "breeding", "nutrition") and "search_documents" in tools:
        deterministic = True
        search("clinical" if intent == "clinical" else intent, a.conf("health_request"))
        if "create_health_call" in tools and gates.signed_in:
            notes.append(f"After the advice, ask exactly: {HEALTH_OFFER_LINE}")
    elif health == "declines_offer":
        deterministic = True
        notes.append("The farmer declined the health call offer: acknowledge briefly and offer further help; do not book.")

    # ── AI call (insemination) ──────────────────────────────────────────────
    tech_reply = a.yes("last_assistant_asked_technician", yes)
    if ai_route or (tech_reply and a.choice("technician_selected", "not_stated") != "not_stated"):
        deterministic = True
        if gates.ai_call_banned:
            notes.append(f"AI call booking is not allowed for this union. Tell the farmer exactly: `{UNION_BANNED_MESSAGE}` Do not ask which technician.")
        elif not gates.signed_in:
            notes.append("Insemination booking needs the farmer's registered profile, which is not available in this session: say so and suggest contacting their milk society.")
        elif not gates.technicians:
            notes.append("No AI technician details are available for this society right now: say so and ask the farmer to try later or contact their society / Amul support. Do not invent technicians.")
        else:
            picked = a.choice("technician_selected", "not_stated")
            tech = next((t for t in gates.technicians if f"{t.name} ({t.mobile})" == picked), None)
            species = _species(a, gates)
            acct = _pick_account(a, gates)
            if tech is None:
                listing = "; ".join(f"{t.name} ({t.mobile})" for t in gates.technicians)
                clarification = f"Ask which AI technician the farmer wants, showing only name and mobile number: {listing}." + ("" if species else " Also ask whether it is for a cow or a buffalo.")
            elif species is None:
                clarification = f"Technician {tech.name} is selected. Ask once whether the insemination is for a cow or a buffalo."
            elif acct is None or not (acct.union_code and acct.society_code and acct.farmer_code):
                notes.append("Booking codes (union/society/farmer) are missing from the profile: tell the farmer their details are not available right now.")
            else:
                add("create_ai_call", {
                    "union_code": acct.union_code, "society_code": acct.society_code, "farmer_code": acct.farmer_code,
                    "user_id": tech.user_id, "species": species,
                }, min(a.conf("technician_selected") or 1.0, a.conf("species_for_booking") or 1.0), "rule")

    # ── Loan ────────────────────────────────────────────────────────────────
    loan = a.choice("loan_request", "none") if "check_loan_eligibility" in tools else "none"
    if loan != "none" or primary == "check_loan_eligibility" or intent == "loan":
        deterministic = True
        if loan == "agrees_to_offer" or (a.yes("last_assistant_offered_loan", yes) and a.yes("farmer_says_yes", yes)):
            add("check_loan_eligibility", {"confirmed": True}, a.conf("loan_request"), "rule")
        elif loan == "declines_offer" or (a.yes("last_assistant_offered_loan", yes) and a.yes("farmer_says_no", yes)):
            notes.append("The farmer declined the micro-loan offer: close politely, do not call the loan tool again.")
        elif loan == "asks_documents_or_terms_only":
            notes.append("Answer from the 'Loan facility information' section of your instructions (facility, documents, terms). Do not decide eligibility.")
        elif "check_loan_eligibility" in tools:
            add("check_loan_eligibility", {"confirmed": False}, a.conf("loan_request") or primary_conf, "rule")

    # ── Personal records ────────────────────────────────────────────────────
    if primary == "get_farmer_milk_collection_details" or (intent == "profile" and a.prob("primary_tool", "get_farmer_milk_collection_details") >= 0.3):
        deterministic = True
        if "get_farmer_milk_collection_details" in tools:
            period = a.choice("milk_period", "not_stated")
            start, end = C.period_to_range(period, query, default_days=settings.milk_default_range_days, max_days=31)
            add("get_farmer_milk_collection_details", {"fromdate": start.isoformat(), "todate": end.isoformat()}, a.conf("milk_period") or primary_conf)
            if period == "not_stated":
                notes.append(f"No period was given, so the last {settings.milk_default_range_days} days were fetched; mention the date range shown.")
        else:
            notes.append("Milk collection records need the farmer's signed-in profile, which is not available: say so briefly.")
    if primary == "get_farmer_bonus_amount":
        deterministic = True
        if "get_farmer_bonus_amount" in tools:
            add("get_farmer_bonus_amount", {}, primary_conf)
        else:
            notes.append("Bonus lookup needs the farmer's signed-in profile, which is not available: say so briefly.")

    # ── Schemes ─────────────────────────────────────────────────────────────
    scope = a.choice("scheme_scope", "not_a_scheme_question") if ("get_union_scheme_data" in tools or "get_vistaar_scheme_info" in tools) else "not_a_scheme_question"
    central = C.deterministic_scheme_code(query) or (a.choice("central_scheme", "none") if scope != "not_a_scheme_question" else "none")
    if central == "none":
        central = None
    scheme_route = (
        intent == "scheme"
        or primary in ("get_union_scheme_data", "get_vistaar_scheme_info")
        or (scope in ("farmer_union_schemes", "central_government_scheme", "both_or_general")
            and a.conf("scheme_scope") >= 0.6 and primary == "none_answer_directly")
    )
    if scheme_route:
        deterministic = True
        if central and "get_vistaar_scheme_info" in tools and scope != "farmer_union_schemes":
            add("get_vistaar_scheme_info", {"scheme_code": central}, a.conf("central_scheme") or 1.0)
        if "get_union_scheme_data" in tools and scope in ("farmer_union_schemes", "both_or_general", "not_a_scheme_question") or (primary == "get_union_scheme_data"):
            if "get_union_scheme_data" in tools:
                add("get_union_scheme_data", {"scheme_name": None if scope == "both_or_general" else query[:120]}, a.conf("scheme_scope") or primary_conf)
        if not any(c.name in ("get_union_scheme_data", "get_vistaar_scheme_info") for c in calls) and "search_documents" in tools:
            search("scheme", primary_conf)

    # ── Market / weather (live data; never search) ──────────────────────────
    want_mandi = primary == "get_vistaar_mandi_prices" or intent == "market" or a.yes("also_get_vistaar_mandi_prices", settings.extra_tool_noul_threshold)
    want_weather = primary == "get_vistaar_weather" or intent == "weather" or a.yes("also_get_vistaar_weather", settings.extra_tool_noul_threshold)
    if want_mandi and "get_vistaar_mandi_prices" in tools and intent != "cattle_trade":
        deterministic = True
        commodity = a.choice("commodity", "none")
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", query)}
        if commodity not in ("none", "other_named_in_message"):
            options = list((answers.get("commodity") or {}).get("probabilities") or {})
            exact = [o for o in options if o.lower() in words]
            if exact and commodity.lower() not in words:
                commodity = min(exact, key=len)
        if commodity == "other_named_in_message":
            span = a.choice("commodity_span", "none") if "commodity_span" in answers else "none"
            commodity = span.title() if span != "none" else "none"
        if commodity == "none":
            clarification = "Ask which crop or commodity the farmer wants the mandi price for."
        else:
            args: dict[str, Any] = {"commodity_name": commodity}
            loc = _location(a)
            if loc:
                args["location"] = loc
            period = a.choice("price_period", "not_stated")
            if period != "not_stated":
                start, end = C.period_to_range(period, query, default_days=7, max_days=30)
                args["price_date"], args["price_date_to"] = _fmt(start), _fmt(end)
            add("get_vistaar_mandi_prices", args, min(a.conf("commodity") or 1.0, a.conf("place_named") or 1.0))
    if want_weather and "get_vistaar_weather" in tools:
        deterministic = True
        args = {}
        loc = _location(a)
        if loc:
            args["location"] = loc
        add("get_vistaar_weather", args, a.conf("place_named") or primary_conf)

    # ── Soil health card ────────────────────────────────────────────────────
    shc = a.choice("shc_request", "none") if "get_vistaar_soil_health_card" in tools else "none"
    if shc != "none" or primary == "get_vistaar_soil_health_card":
        deterministic = True
        if shc == "general_scheme_question" and "get_vistaar_scheme_info" in tools:
            add("get_vistaar_scheme_info", {"scheme_code": "shc"}, a.conf("shc_request"))
        elif shc == "follow_up_on_card_context" and gates.has_shc_context:
            notes.append("Answer from the private Soil Health Card context already in the user message; do not say the card must be fetched.")
        elif "get_vistaar_soil_health_card" in tools:
            cycle = a.choice("shc_cycle", "not_stated")
            if cycle == "not_stated":
                clarification = "Ask only which Soil Health Card cycle the farmer wants (for example 2024-25 or 2025-26). Never ask for a mobile number."
            else:
                add("get_vistaar_soil_health_card", {"cycle": cycle}, a.conf("shc_cycle"))

    # ── Vet office lookup ───────────────────────────────────────────────────
    if primary == "find_nearby_vet_offices" and "find_nearby_vet_offices" in tools:
        deterministic = True
        span = a.choice("place_span", "none") if "place_span" in answers else "none"
        add("find_nearby_vet_offices", {"taluka": "" if span == "none" else span}, primary_conf)

    # ── Retrieval (rules 3 / 3c) ─────────────────────────────────────────────
    if not calls and not clarification and not notes:
        topic = a.choice("search_topic", "general")
        if intent == "cattle_trade" and "search_documents" in tools:
            search("cattle_trade", a.conf("intent") or 1.0)
        elif primary == "search_documents" or intent in ("clinical", "nutrition", "breeding", "crop"):
            if "search_documents" in tools:
                search(topic if topic in ("clinical", "nutrition", "breeding", "crop", "scheme", "general") else intent, primary_conf)
                deterministic = deterministic or primary == "search_documents"
        elif intent == "language_switch":
            notes.append("The farmer asks to switch language: acknowledge briefly; the system translates output downstream. Do not search.")
        elif intent == "out_of_scope":
            notes.append("Out of scope: decline briefly and redirect to agriculture / livestock topics. Do not search.")
        elif intent in ("greeting_smalltalk",):
            notes.append("Greeting / small talk: reply briefly and warmly as Sarlaben and invite a farming question.")
        elif intent in ("services", "profile"):
            notes.append("Answer from the Farmer Profile context if present; otherwise ask clearly for the required identifier. Do not search documents.")
    elif (a.yes("also_search_documents", settings.extra_tool_noul_threshold) and "search_documents" in tools
          and intent in ("clinical", "nutrition", "breeding", "crop", "cattle_trade", "scheme")):
        search(a.choice("search_topic", "general"), a.noul("also_search_documents"))

    if clarification:
        notes.append(clarification)

    used = [c for c in a.used if c > 0]
    plan_conf = min(used) if used else primary_conf
    plan = Plan(intent=intent, tool_calls=calls, compose_notes=notes, clarification=clarification,
                confidence=plan_conf, answers=answers)

    # ── Confidence gate (accuracy floor) ────────────────────────────────────
    # A route decided only by Jev's tool/intent choice must clear the threshold;
    # a route fixed by a slot answer or a code rule (alias map, profile facts) is exempt.
    route_conf = max(primary_conf, a.conf("intent"))
    if not deterministic and route_conf < settings.tool_choice_min_confidence:
        if settings.low_confidence_policy == "escalate_llm":
            plan.escalate = True
            plan.escalate_reason = f"route confidence {route_conf:.2f} < {settings.tool_choice_min_confidence}"
        elif settings.low_confidence_policy == "search_documents" and "search_documents" in tools and not calls:
            search(a.choice("search_topic", "general"), primary_conf)
            plan.tool_calls = calls
        elif settings.low_confidence_policy == "ask_clarification" and not calls:
            plan.compose_notes.append("The request is ambiguous: ask one brief clarifying question about what the farmer needs.")
    return plan
