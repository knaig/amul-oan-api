"""Turn state + the speculative fan-out question set sent to Jev.

Every judgement the legacy first LLM request made implicitly is written here as
an explicit closed-set question (see docs/JEV_PLANNER.md, table "What call #1
does"). All questions are evaluated in parallel against ONE state; code reads
only the answers the chosen route needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agents.deps import FarmerContext
from agents.tools.models.union import resolve_supported_unions
from agents.tools.union_schemes import SUPPORTED_SCHEME_UNIONS
from app.config import settings as app_settings
from app.planner import candidates as C
from app.planner.config import PlannerSettings

ALL_TOOLS = [
    "search_documents",
    "find_nearby_vet_offices",
    "create_ai_call",
    "create_health_call",
    "get_farmer_milk_collection_details",
    "get_farmer_bonus_amount",
    "get_union_scheme_data",
    "check_loan_eligibility",
    "get_vistaar_weather",
    "get_vistaar_mandi_prices",
    "get_vistaar_scheme_info",
    "get_vistaar_soil_health_card",
]

TOOL_DESCRIPTIONS = {
    "search_documents": "look up veterinary / dairy / crop knowledge documents: disease, symptoms, treatment, feeding, fodder, breeding, heat, calf care, milk quality, crop practices, buying or selling cattle, general how-to advice",
    "find_nearby_vet_offices": "find the government veterinary hospital, dispensary or first-aid centre for the farmer's village or taluka (where to take the animal)",
    "create_ai_call": "book an artificial insemination (AI, beej daan) visit by an insemination technician for breeding; NOT for a sick animal",
    "create_health_call": "book a doctor / veterinary health visit for a sick, injured, collapsed or unwell animal, or when the farmer agrees to book such a visit",
    "get_farmer_milk_collection_details": "fetch the farmer's OWN milk collection records (litres, fat, SNF, amount, deductions) for a date range from the dairy",
    "get_farmer_bonus_amount": "fetch the farmer's OWN bonus amount paid by the dairy union",
    "get_union_scheme_data": "details of the welfare schemes offered by the farmer's OWN Amul milk union (insurance, cattle welfare, subsidies from the dairy)",
    "check_loan_eligibility": "micro loan / credit eligibility or approval from the cooperative bank for this farmer",
    "get_vistaar_weather": "live weather forecast (rain, temperature, humidity, wind) for a district",
    "get_vistaar_mandi_prices": "live mandi (market) prices for a crop or commodity such as onion, cotton, wheat, groundnut",
    "get_vistaar_scheme_info": "details of a CENTRAL / national government agriculture scheme such as Kisan Credit Card, PM-KISAN, crop insurance (PMFBY), Soil Health Card scheme",
    "get_vistaar_soil_health_card": "fetch the farmer's OWN Soil Health Card report for a cycle year (soil test values, nutrient levels)",
    "none_answer_directly": "no data lookup is needed: greeting, thanks, small talk, a question answered from the farmer's profile shown to the assistant, a request to change language, a question outside agriculture, or a follow-up that the conversation already answers",
}

INTENTS = {
    "clinical": "animal disease, symptoms, illness, injury, treatment, medicine, vaccination, deworming",
    "nutrition": "feeding, fodder, ration, minerals, water, milk yield improvement through diet",
    "breeding": "heat, insemination, pregnancy, calving, repeat breeding, infertility",
    "crop": "crop cultivation, seeds, fertiliser, pests, irrigation, farm management",
    "scheme": "government or dairy-union schemes, subsidies, insurance, benefits",
    "market": "mandi / market price of a crop or commodity",
    "weather": "weather, rain, temperature forecast",
    "cattle_trade": "buying, selling, or finding cows or buffaloes, cattle marketplace, Amul Pashudhan",
    "services": "dairy services: AI receipts, ear tags, tracking numbers, society services, vet visit booking, technician",
    "profile": "the farmer's own account, animals, society, union, milk or bonus data",
    "loan": "micro loan, credit, borrowing money",
    "language_switch": "asks to change the reply language",
    "greeting_smalltalk": "greeting, thanks, identity question, chit-chat",
    "out_of_scope": "unrelated to agriculture, livestock, dairy or farming",
}


@dataclass
class TurnGates:
    """What the legacy prepare= hooks and settings would expose this turn."""

    signed_in: bool
    accounts: list[C.Account]
    technicians: list[C.Technician]
    ai_call_banned: bool
    union_scheme_supported: bool
    loan_enabled: bool
    shc_enabled: bool
    has_shc_context: bool
    persona: str
    disabled: set[str] = field(default_factory=set)

    def enabled_tools(self) -> list[str]:
        if self.persona == "doctor":
            return [t for t in ["search_documents"] if t not in self.disabled]
        tools = []
        for name in ALL_TOOLS:
            if name in self.disabled:
                continue
            if name in ("get_farmer_milk_collection_details", "get_farmer_bonus_amount") and not self.signed_in:
                continue
            if name == "get_union_scheme_data" and not self.union_scheme_supported:
                continue
            if name == "check_loan_eligibility" and not self.loan_enabled:
                continue
            if name == "get_vistaar_soil_health_card" and not (self.shc_enabled and self.signed_in):
                continue
            tools.append(name)
        return tools


def gates_for(deps: FarmerContext, settings: PlannerSettings) -> TurnGates:
    accounts = C.parse_accounts(deps.farmer_info)
    supported = resolve_supported_unions([u for u in (deps.farmer_unions or []) if u], SUPPORTED_SCHEME_UNIONS)
    return TurnGates(
        signed_in=bool(deps.mobile),
        accounts=accounts,
        technicians=C.parse_technicians(deps.farmer_info),
        ai_call_banned=C.ai_call_banned(deps.farmer_info),
        union_scheme_supported=bool(supported),
        loan_enabled=bool(getattr(app_settings, "loan_feature_enabled", False)),
        shc_enabled=bool(getattr(app_settings, "vistaar_shc_enabled", False)),
        has_shc_context=bool((deps.soil_health_card_context or "").strip()),
        persona=deps.persona,
        disabled=set(settings.disabled_tools or []),
    )


def build_state(deps: FarmerContext, gates: TurnGates, history_pairs: list[tuple[str, str]], *, original_query: Optional[str] = None) -> dict[str, Any]:
    """Compact state: only what the questions need (Jev accuracy drops with filler)."""
    conversation = [{"farmer": u, "assistant": a} for u, a in history_pairs]
    profile: dict[str, Any] = {"signed_in": gates.signed_in}
    if gates.signed_in:
        profile["district"] = deps.farmer_district or "unknown"
        profile["accounts"] = [a.label() for a in gates.accounts] or ["no dairy account found on this mobile"]
        if gates.ai_call_banned:
            profile["ai_call_booking"] = "not allowed for this farmer's union"
        elif gates.technicians:
            profile["ai_technicians"] = [f"{t.name} ({t.mobile})" for t in gates.technicians]
        else:
            profile["ai_technicians"] = "none available"
        profile["has_soil_health_card_context"] = gates.has_shc_context
    state: dict[str, Any] = {
        "today": C.today_ist().isoformat(),
        "farmer_message": deps.query,
        "conversation_before_this_message": conversation or "none (first message)",
        "last_assistant_message": conversation[-1]["assistant"] if conversation else "none",
        "farmer_profile": profile,
    }
    if original_query and original_query.strip() != (deps.query or "").strip():
        state["farmer_message_original_language"] = original_query
    return state


def _choice(instructions: Any, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _noul(instructions: Any, true: Optional[str] = None, false: Optional[str] = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def build_questions(deps: FarmerContext, gates: TurnGates) -> dict[str, dict]:
    """The full speculative fan-out. Question ids are for code only."""
    tools = gates.enabled_tools()
    q: dict[str, dict] = {}
    msg = "`farmer_message`"
    conv = "`conversation_before_this_message`"

    if gates.persona == "doctor":
        q["needs_search"] = _noul(
            f"Does answering {msg} require looking up veterinary or agricultural reference documents (disease, treatment, dose, feeding, breeding, crop facts)?",
            true="a factual veterinary / agricultural question that documents can support",
            false="greeting, thanks, small talk, or a question the conversation already answered",
        )
        q["search_topic"] = _choice(f"Which topic does {msg} belong to?", {k: v for k, v in INTENTS.items() if k in ("clinical", "nutrition", "breeding", "crop")} | {"other": "none of these"})
        q["species_named"] = _species_question(msg)
        return q

    q["intent"] = _choice(
        {"question": f"Read {msg} in the context of {conv}. Which single category best describes what the farmer wants now?"},
        INTENTS,
    )
    q["primary_tool"] = _choice(
        {
            "question": f"Which ONE action should the assistant take first to answer {msg}? Use {conv} to resolve short replies such as 'yes', 'buffalo', a person's name, or a year.",
            "rules": [
                "A sick, injured or unwell animal where the farmer explicitly asks for a doctor / vet / health call, or agrees to an offered health call -> create_health_call.",
                "A sick animal described without asking for a visit -> search_documents (advice first).",
                "Insemination / heat / 'AI call' booking, or choosing a technician -> create_ai_call.",
                "Mandi price -> get_vistaar_mandi_prices; weather -> get_vistaar_weather; never search documents for these.",
                "Buying or selling a cow or buffalo -> search_documents.",
                "Amul union / dairy schemes -> get_union_scheme_data; central government scheme -> get_vistaar_scheme_info.",
                "The farmer's own milk records -> get_farmer_milk_collection_details; own bonus -> get_farmer_bonus_amount; own soil card -> get_vistaar_soil_health_card.",
                "Greeting, thanks, language change, off-topic, or a profile fact -> none_answer_directly.",
            ],
        },
        {name: TOOL_DESCRIPTIONS[name] for name in [*tools, "none_answer_directly"]},
    )
    for name in ("get_vistaar_weather", "get_vistaar_mandi_prices", "get_union_scheme_data", "get_vistaar_scheme_info", "search_documents"):
        if name in tools:
            q[f"also_{name}"] = _noul(
                f"Besides the main action, does {msg} ALSO ask for this: {TOOL_DESCRIPTIONS[name]}?",
                true="the message clearly asks for this as well (two requests in one message)",
                false="not asked, or it is the only thing asked",
            )

    q["search_topic"] = _choice(
        f"If documents are searched for {msg}, which topic fits best?",
        {k: INTENTS[k] for k in ("clinical", "nutrition", "breeding", "crop", "cattle_trade", "scheme")} | {"general": "general agriculture / dairy information"},
    )
    q["species_named"] = _species_question(msg)

    # Health call booking slots.
    if "create_health_call" in tools:
        q["health_request"] = _choice(
            {"question": f"About a veterinary / doctor visit for an animal: what does {msg} do, given {conv}?"},
            {
                "explicit_booking_request": "the farmer asks for a doctor, vet, health call or visit to be booked now",
                "confirms_earlier_offer": "the assistant earlier asked whether to book a health call and the farmer now agrees (yes, ok, book it, please)",
                "describes_problem_only": "the farmer describes sickness or symptoms but does not ask for a visit",
                "declines_offer": "the assistant offered a health call and the farmer refuses",
                "none": "not about an animal health visit",
            },
        )
        q["case_severity"] = _choice(
            f"How severe is the animal's condition described in {msg} and {conv}?",
            {
                "emergency": "collapsed, unable to stand, bloat, poisoning, heavy bleeding, difficult calving, prolapse, high fever with distress, or the farmer says urgent / emergency",
                "normal": "mild or routine: reduced appetite, mild fever, lameness, mastitis suspicion, wound, cough, routine check",
            },
        )
        q["species_for_booking"] = _choice(
            f"For which animal is the visit or booking in {msg} (use {conv} for a short reply like 'buffalo')?",
            {"cow": None, "buffalo": None, "not_stated": "the farmer has not said whether it is a cow or a buffalo"},
        )
        if len(gates.accounts) > 1:
            q["account_for_booking"] = _choice(
                f"Which of the farmer's dairy accounts (`farmer_profile.accounts`) does {msg} refer to?",
                {a.label(): None for a in gates.accounts} | {"not_stated": "the farmer did not indicate which account"},
            )

    # AI (insemination) booking slots.
    if "create_ai_call" in tools:
        q["ai_request"] = _choice(
            {"question": f"About artificial insemination (AI, beej daan) booking: what does {msg} do, given {conv}?"},
            {
                "asks_to_book_insemination": "the farmer wants an insemination / AI technician visit booked (animal in heat, breeding)",
                "selects_technician": "the assistant listed technicians and the farmer now names or picks one",
                "answers_species_question": "the assistant asked cow or buffalo for the AI booking and the farmer answers",
                "none": "not about insemination booking",
            },
        )
        if gates.technicians:
            q["technician_selected"] = _choice(
                f"Which technician from `farmer_profile.ai_technicians` does the farmer pick in {msg}?",
                {f"{t.name} ({t.mobile})": None for t in gates.technicians} | {"not_stated": "no technician is named or picked"},
            )

    if "check_loan_eligibility" in tools:
        q["loan_request"] = _choice(
            {"question": f"About a micro loan / credit: what does {msg} do, given {conv}?"},
            {
                "asks_for_loan_or_eligibility": "asks for a loan, whether they can get one, how much, or to apply",
                "agrees_to_offer": "the assistant offered a loan amount and asked whether to proceed; the farmer says yes",
                "declines_offer": "the assistant offered a loan and the farmer says no",
                "asks_documents_or_terms_only": "asks only what documents are needed, the interest, or what the loan is",
                "none": "not about a loan",
            },
        )
        q["last_assistant_offered_loan"] = _noul(
            "Does `last_assistant_message` tell the farmer they are eligible for a micro loan and ask whether they want to avail it?",
        )

    if "get_union_scheme_data" in tools or "get_vistaar_scheme_info" in tools:
        q["scheme_scope"] = _choice(
            f"If {msg} is about a scheme, whose scheme is it?",
            {
                "farmer_union_schemes": "the farmer's own Amul milk union / dairy cooperative schemes (insurance, cattle welfare, union subsidy, 'my union', Banas/Kaira/Sabar/Sumul schemes)",
                "central_government_scheme": "a national / central government scheme (KCC, PM-KISAN, crop insurance, soil health card, irrigation, mechanisation subsidy)",
                "both_or_general": "asks generally which schemes are available, without saying whose",
                "not_a_scheme_question": "not about schemes at all",
            },
        )
        q["central_scheme"] = _choice(
            f"Which central government scheme does {msg} name or clearly mean?",
            C.scheme_options(),
        )

    if "get_vistaar_mandi_prices" in tools:
        q["commodity"] = _choice(
            f"Which crop or commodity does the farmer want the market price of in {msg}?",
            C.commodity_options(deps.query),
        )
        q["price_period"] = _choice(
            f"For which time period does {msg} want prices?",
            C.PERIODS,
        )
    if "get_vistaar_mandi_prices" in tools or "get_vistaar_weather" in tools:
        q["place_named"] = _choice(
            f"Which Gujarat district does the farmer name (directly or via a town in it) in {msg}? Pick not_stated when no place is written; do not infer from the profile.",
            C.district_options(deps.query),
        )
    spans = C.noun_spans(deps.query)
    if spans and ("get_vistaar_mandi_prices" in tools or "get_vistaar_weather" in tools or "find_nearby_vet_offices" in tools):
        q["place_span"] = _choice(
            f"If the farmer names a place (town, village, taluka, district) in {msg}, which of these exact words from the message is that place name?",
            {s: None for s in spans} | {"none": "no place name appears in the message"},
        )
    if spans and "get_vistaar_mandi_prices" in tools:
        q["commodity_span"] = _choice(
            f"If the farmer names a crop or commodity in {msg}, which of these exact words from the message is it?",
            {s: None for s in spans} | {"none": "no crop or commodity appears in the message"},
        )

    if "get_farmer_milk_collection_details" in tools:
        q["milk_period"] = _choice(
            f"For which time period does {msg} ask about milk collection / payment records?",
            C.PERIODS,
        )

    if "get_vistaar_soil_health_card" in tools:
        q["shc_request"] = _choice(
            f"About the Soil Health Card: what does {msg} ask, given {conv}?",
            {
                "wants_own_card_or_report": "show / check / get MY soil health card, soil test report, or answers a cycle-year question the assistant asked",
                "general_scheme_question": "what the Soil Health Card scheme is, eligibility, how to apply",
                "follow_up_on_card_context": "asks about their soil nutrients or fertiliser after the card was already shown",
                "none": "not about the soil health card",
            },
        )
        q["shc_cycle"] = _choice(
            f"Which soil health card cycle year does {msg} name (use {conv} for a bare year reply)?",
            C.shc_cycle_options(),
        )

    # Dialogue-state facts, asked directly (Jev is literal; code composes them).
    q["farmer_says_yes"] = _noul(
        f"Is {msg} an agreement or confirmation (yes, ok, sure, please do, go ahead, ha) to what `last_assistant_message` proposed?",
    )
    q["farmer_says_no"] = _noul(
        f"Is {msg} a refusal (no, not now, later, don't) of what `last_assistant_message` proposed?",
    )
    q["last_assistant_offered_health_call"] = _noul(
        "Does `last_assistant_message` ask the farmer whether they want to book a health call / doctor visit?",
    )
    q["last_assistant_asked_technician"] = _noul(
        "Does `last_assistant_message` list insemination technicians or ask which technician the farmer wants?",
    )
    q["last_assistant_asked_species"] = _noul(
        "Does `last_assistant_message` ask whether the animal is a cow or a buffalo?",
    )
    return q


def _species_question(msg: str) -> dict:
    return _choice(
        f"Which animal does the farmer explicitly name in {msg}? Pick not_named if no animal word is written (the assistant then assumes dairy cattle).",
        {
            "cow": "cow, cows, gai, heifer, calf of a cow",
            "buffalo": "buffalo, bhens, buffaloes",
            "goat": None,
            "sheep": None,
            "poultry": "hen, chicken, birds",
            "other": "another animal such as dog, horse, camel",
            "not_named": "no animal is named",
        },
    )
