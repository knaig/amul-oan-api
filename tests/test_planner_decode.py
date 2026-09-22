"""The Jev decoder reproduces the prompt's routing rules from typed answers (offline)."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import pytest

from agents.deps import FarmerContext
from app.planner.config import PlannerSettings
from app.planner.decode import HEALTH_OFFER_LINE, decode
from app.planner.questions import build_questions, build_state, gates_for

FARMER_INFO = """# Farmer Context

## Farmer 1
- **Farmer name:** Rameshbhai Patel
- **Farmer code:** 00123
- **Society name:** Anand Dudh Mandali
- **Society code:** 045
- **Union name:** Banas
- **Union code:** 01
- **District:** Anand
### Herd summary
- **Total cows:** 2
- **Total buffalo:** 0
### Available AI technicians
- **Name:** Kiran Patel | **Mobile number:** 9999911111 | **user_id:** T1
- **Name:** Meena Shah | **Mobile number:** 9999922222 | **user_id:** T2
"""


def _deps(query, *, signed_in=True, persona="farmer"):
    return FarmerContext(
        query=query, session_id="s1", lang_code="en", persona=persona,
        farmer_info=FARMER_INFO if signed_in else "", farmer_unions=["banas"] if signed_in else [],
        farmer_district="anand" if signed_in else None, mobile="9876543210" if signed_in else None,
    )


def _settings(**kw):
    return PlannerSettings(**kw)


def choice(option, confidence=0.9, **probs):
    return {"type": "choice", "choice": option, "confidence": confidence, "probabilities": {option: confidence, **probs}}


def noul(p):
    return {"type": "noul", "noul": p}


def base_answers(**over):
    a = {
        "intent": choice("clinical"), "primary_tool": choice("search_documents"),
        "search_topic": choice("clinical"), "species_named": choice("cow"),
        "health_request": choice("none"), "case_severity": choice("normal"),
        "species_for_booking": choice("not_stated"), "ai_request": choice("none"), "technician_selected": choice("not_stated"),
        "loan_request": choice("none"), "last_assistant_offered_loan": noul(0.02),
        "scheme_scope": choice("not_a_scheme_question"), "central_scheme": choice("none"),
        "commodity": choice("none"), "price_period": choice("not_stated"), "place_named": choice("not_stated"),
        "milk_period": choice("not_stated"), "shc_request": choice("none"), "shc_cycle": choice("not_stated"),
        "farmer_says_yes": noul(0.05), "farmer_says_no": noul(0.05), "last_assistant_offered_health_call": noul(0.02),
        "last_assistant_asked_technician": noul(0.02), "last_assistant_asked_species": noul(0.02),
        "also_get_vistaar_weather": noul(0.05), "also_get_vistaar_mandi_prices": noul(0.05), "also_search_documents": noul(0.05),
        "also_get_union_scheme_data": noul(0.05), "also_get_vistaar_scheme_info": noul(0.05),
    }
    a.update(over)
    return a


def plan_for(query, answers, *, settings=None, signed_in=True, persona="farmer"):
    deps = _deps(query, signed_in=signed_in, persona=persona)
    settings = settings or _settings()
    return decode(deps, gates_for(deps, settings), answers, settings), deps


def test_sick_animal_searches_then_offers_health_call():
    plan, _ = plan_for("my cow has fever and is not eating", base_answers(health_request=choice("describes_problem_only")))
    names = plan.tool_names()
    assert names and set(names) == {"search_documents"}
    assert len(names) == 2  # fan-out 2
    assert "cow" in plan.tool_calls[0].args["query"]
    assert any(HEALTH_OFFER_LINE in n for n in plan.compose_notes)
    assert not plan.escalate


def test_explicit_vet_request_books_health_call_with_profile_codes():
    plan, _ = plan_for("please send a doctor my cow collapsed", base_answers(
        primary_tool=choice("create_health_call"), health_request=choice("explicit_booking_request"),
        case_severity=choice("emergency"), species_for_booking=choice("cow")))
    assert plan.tool_names() == ["create_health_call"]
    args = plan.tool_calls[0].args
    assert (args["union_code"], args["society_code"], args["farmer_code"]) == ("01", "045", "00123")
    assert args["species"] == "cow" and args["case_type"] == "emergency"


def test_yes_after_offer_books_health_call_using_herd_species_default():
    plan, _ = plan_for("yes", base_answers(
        intent=choice("services"), primary_tool=choice("none_answer_directly", 0.5),
        health_request=choice("confirms_earlier_offer"), last_assistant_offered_health_call=noul(0.95), farmer_says_yes=noul(0.97)))
    assert plan.tool_names() == ["create_health_call"]
    assert plan.tool_calls[0].args["species"] == "cow"  # herd has cows only
    assert plan.tool_calls[0].args["case_type"] == "normal"


def test_species_unknown_asks_instead_of_booking():
    deps_info = FARMER_INFO.replace("- **Total buffalo:** 0", "- **Total buffalo:** 3")
    deps = _deps("book a health call")
    deps.farmer_info = deps_info
    s = _settings()
    plan = decode(deps, gates_for(deps, s), base_answers(primary_tool=choice("create_health_call"), health_request=choice("explicit_booking_request"), species_named=choice("not_named")), s)
    assert plan.tool_calls == []
    assert plan.clarification and "cow or a buffalo" in plan.clarification


def test_mandi_price_with_named_place_and_period():
    plan, _ = plan_for("onion price in Junagadh last week", base_answers(
        intent=choice("market"), primary_tool=choice("get_vistaar_mandi_prices"), commodity=choice("Onion"),
        place_named=choice("Junagadh"), price_period=choice("last_7_days")))
    assert plan.tool_names() == ["get_vistaar_mandi_prices"]
    args = plan.tool_calls[0].args
    assert args["commodity_name"] == "Onion" and args["location"] == "Junagadh"
    assert args["price_date"] < args["price_date_to"] or args["price_date"] != args["price_date_to"]


def test_unlisted_commodity_falls_back_to_span_from_message():
    plan, _ = plan_for("what is the rate of dragon fruit today", base_answers(
        intent=choice("market"), primary_tool=choice("get_vistaar_mandi_prices"), commodity=choice("other_named_in_message"),
        commodity_span=choice("dragon fruit"), price_period=choice("today")))
    assert plan.tool_calls[0].args["commodity_name"] == "Dragon Fruit"


def test_weather_and_mandi_in_one_message_fans_out():
    plan, _ = plan_for("will it rain tomorrow and what is cotton price", base_answers(
        intent=choice("weather"), primary_tool=choice("get_vistaar_weather"), commodity=choice("Cotton"),
        also_get_vistaar_mandi_prices=noul(0.92)))
    assert set(plan.tool_names()) == {"get_vistaar_weather", "get_vistaar_mandi_prices"}
    assert "location" not in plan.tool_calls[0].args  # farmer district applies inside the tool


def test_central_scheme_alias_is_deterministic():
    plan, _ = plan_for("tell me about kisan credit card", base_answers(
        intent=choice("scheme"), primary_tool=choice("get_vistaar_scheme_info"), scheme_scope=choice("central_government_scheme"),
        central_scheme=choice("pmkisan", 0.4)))  # Jev unsure; alias map wins
    assert ("get_vistaar_scheme_info", {"scheme_code": "kcc"}) in [(c.name, c.args) for c in plan.tool_calls]


def test_union_scheme_question_uses_union_tool_for_supported_union():
    plan, _ = plan_for("what insurance does my union give", base_answers(
        intent=choice("scheme"), primary_tool=choice("get_union_scheme_data"), scheme_scope=choice("farmer_union_schemes")))
    assert plan.tool_names() == ["get_union_scheme_data"]
    assert plan.tool_calls[0].args["scheme_name"]


def test_milk_records_last_month_resolve_to_full_previous_month():
    plan, _ = plan_for("show my milk for last month", base_answers(
        intent=choice("profile"), primary_tool=choice("get_farmer_milk_collection_details"), milk_period=choice("last_month")))
    args = plan.tool_calls[0].args
    assert args["fromdate"].endswith("-01") and args["todate"] > args["fromdate"]


def test_milk_records_hidden_when_not_signed_in():
    plan, _ = plan_for("show my milk records", base_answers(
        intent=choice("profile"), primary_tool=choice("get_farmer_milk_collection_details")), signed_in=False)
    assert plan.tool_calls == []
    assert any("signed-in" in n for n in plan.compose_notes)


def test_loan_two_step_flow(monkeypatch):
    import app.config
    monkeypatch.setattr(app.config.settings, "loan_feature_enabled", True)
    first, _ = plan_for("can i get a loan", base_answers(intent=choice("loan"), loan_request=choice("asks_for_loan_or_eligibility"), primary_tool=choice("check_loan_eligibility")))
    assert first.tool_names() == ["check_loan_eligibility"] and first.tool_calls[0].args == {"confirmed": False}
    second, _ = plan_for("yes please", base_answers(intent=choice("loan"), loan_request=choice("agrees_to_offer"), last_assistant_offered_loan=noul(0.96), farmer_says_yes=noul(0.98)))
    assert second.tool_calls[0].args == {"confirmed": True}
    declined, _ = plan_for("no thanks", base_answers(intent=choice("loan"), loan_request=choice("declines_offer")))
    assert declined.tool_calls == [] and any("declined" in n for n in declined.compose_notes)


def test_ai_call_lists_technicians_then_books_on_selection():
    ask, _ = plan_for("my cow is in heat book insemination", base_answers(intent=choice("breeding"), primary_tool=choice("create_ai_call"), ai_request=choice("asks_to_book_insemination"), species_for_booking=choice("cow")))
    assert ask.tool_calls == [] and "Kiran Patel (9999911111)" in ask.clarification
    book, _ = plan_for("Meena", base_answers(intent=choice("services"), primary_tool=choice("create_ai_call"), ai_request=choice("selects_technician"),
                                             technician_selected=choice("Meena Shah (9999922222)"), species_for_booking=choice("cow"), last_assistant_asked_technician=noul(0.95)))
    assert book.tool_names() == ["create_ai_call"] and book.tool_calls[0].args["user_id"] == "T2"


def test_lookup_question_never_piggybacks_a_health_call():
    plan, _ = plan_for("where is the nearest veterinary dispensary", base_answers(
        intent=choice("services"), primary_tool=choice("find_nearby_vet_offices"), health_request=choice("explicit_booking_request", 0.7)))
    assert plan.tool_names() == ["find_nearby_vet_offices"]


def test_insemination_request_never_books_a_health_call():
    plan, _ = plan_for("My cow is in heat, book insemination", base_answers(
        intent=choice("breeding"), primary_tool=choice("create_ai_call"), ai_request=choice("asks_to_book_insemination"),
        health_request=choice("explicit_booking_request", 0.8), species_for_booking=choice("cow")))
    assert "create_health_call" not in plan.tool_names() and plan.clarification and "Kiran Patel" in plan.clarification


def test_commodity_prefers_the_word_the_farmer_used():
    ans = base_answers(intent=choice("market"), primary_tool=choice("get_vistaar_mandi_prices"))
    ans["commodity"] = {"type": "choice", "choice": "Cotton (Kapas)", "confidence": 0.6, "probabilities": {"Cotton (Kapas)": 0.55, "Cotton": 0.4, "Cotton Seed": 0.05}}
    plan, _ = plan_for("and cotton?", ans)
    assert plan.tool_calls[0].args["commodity_name"] == "Cotton"


def test_ai_call_banned_union_never_books():
    deps = _deps("book AI call for my buffalo")
    deps.farmer_info = FARMER_INFO + "\n### AI call booking\n- AI call booking is not allowed for this union.\n"
    s = _settings()
    plan = decode(deps, gates_for(deps, s), base_answers(primary_tool=choice("create_ai_call"), ai_request=choice("asks_to_book_insemination"), species_for_booking=choice("buffalo")), s)
    assert plan.tool_calls == [] and any("Kindly contact your Milk Society" in n for n in plan.compose_notes)


def test_personal_data_question_does_not_also_search():
    plan, _ = plan_for("what is my bonus amount", base_answers(intent=choice("profile"), primary_tool=choice("get_farmer_bonus_amount"), also_search_documents=noul(0.85),
                                                            scheme_scope=choice("farmer_union_schemes", 0.41), search_topic=choice("scheme")))
    assert plan.tool_names() == ["get_farmer_bonus_amount"]


def test_out_of_scope_and_greeting_need_no_tools():
    for intent in ("out_of_scope", "greeting_smalltalk", "language_switch"):
        plan, _ = plan_for("hello", base_answers(intent=choice(intent), primary_tool=choice("none_answer_directly")))
        assert plan.tool_calls == [] and plan.compose_notes and not plan.escalate


def test_cattle_trade_always_searches():
    plan, _ = plan_for("I want to sell my buffalo", base_answers(intent=choice("cattle_trade"), primary_tool=choice("none_answer_directly", 0.3)))
    assert plan.tool_names() and set(plan.tool_names()) == {"search_documents"}
    assert "Amul cattle trade" in " ".join(c.args["query"] for c in plan.tool_calls)


def test_low_confidence_escalates_to_legacy_planner_by_default():
    plan, _ = plan_for("hmm", base_answers(intent=choice("services", 0.3), primary_tool=choice("none_answer_directly", 0.2)))
    assert plan.escalate and "0.30" in plan.escalate_reason


def test_slot_decided_routes_are_exempt_from_the_gate():
    plan, _ = plan_for("yes", base_answers(intent=choice("services", 0.2), primary_tool=choice("none_answer_directly", 0.2),
                                           health_request=choice("confirms_earlier_offer", 0.9), last_assistant_offered_health_call=noul(0.95), farmer_says_yes=noul(0.97)))
    assert not plan.escalate and plan.tool_names() == ["create_health_call"]


def test_low_confidence_policy_search_documents():
    plan, _ = plan_for("hmm cow", base_answers(intent=choice("services", 0.3), primary_tool=choice("none_answer_directly", 0.2)),
                       settings=_settings(low_confidence_policy="search_documents"))
    assert not plan.escalate and plan.tool_names()[0] == "search_documents"


def test_disabled_tool_is_never_planned():
    plan, _ = plan_for("onion price", base_answers(intent=choice("market"), primary_tool=choice("get_vistaar_mandi_prices"), commodity=choice("Onion")),
                       settings=_settings(disabled_tools=["get_vistaar_mandi_prices"]))
    assert "get_vistaar_mandi_prices" not in plan.tool_names()


def test_doctor_persona_only_searches():
    plan, _ = plan_for("mastitis antibiotic dose for buffalo", {"needs_search": noul(0.95), "search_topic": choice("clinical"), "species_named": choice("buffalo")}, persona="doctor")
    assert plan.tool_names() and set(plan.tool_names()) == {"search_documents"}


def test_questions_respect_gates_and_state_is_compact():
    deps = _deps("onion price")
    s = _settings(disabled_tools=["create_ai_call"])
    gates = gates_for(deps, s)
    q = build_questions(deps, gates)
    assert "primary_tool" in q and "create_ai_call" not in q["primary_tool"]["criteria"]
    assert "ai_request" not in q and "technician_selected" not in q
    assert "commodity" in q and len(q["commodity"]["criteria"]) <= 255
    state = build_state(deps, gates, [("hi", "hello")])
    assert state["farmer_message"] == "onion price" and "Farmer Context" not in str(state)
    anon = _deps("bonus", signed_in=False)
    assert "get_farmer_bonus_amount" not in build_questions(anon, gates_for(anon, s))["primary_tool"]["criteria"]
