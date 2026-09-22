import os
from datetime import date

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.planner import candidates as C
from app.planner.keywords import keywordize, search_queries


def test_explicit_dates_and_ranges():
    today = date(2026, 9, 22)
    assert C.explicit_dates("milk from 15 August to 20 August", today) == [date(2026, 8, 15), date(2026, 8, 20)]
    assert C.explicit_dates("records for 2026-09-01", today) == [date(2026, 9, 1)]
    assert C.explicit_dates("01/09/2026 to 10/09/2026", today) == [date(2026, 9, 1), date(2026, 9, 10)]
    assert C.period_to_range("explicit_dates", "15 August to 20 August", default_days=7, today=today) == (date(2026, 8, 15), date(2026, 8, 20))
    assert C.period_to_range("last_month", "", default_days=7, today=today) == (date(2026, 8, 1), date(2026, 8, 31))
    start, end = C.period_to_range("not_stated", "", default_days=7, today=today)
    assert (end - start).days == 7 and end == today
    start, end = C.period_to_range("explicit_dates", "1 January to 30 June", default_days=7, max_days=31, today=today)
    assert (end - start).days == 31


def test_keywordize_respects_validator_limits():
    q = keywordize("Please tell me what should I give to my cow because she is not eating anything since yesterday and looks weak")
    assert len(q.split()) <= 12 and "please" not in q and "cow" in q
    qs = search_queries("my buffalo has mastitis", topic="clinical", fanout=2)
    assert qs[0] == "buffalo mastitis" and "treatment" in qs[1]
    assert search_queries("fodder quantity", topic="nutrition", fanout=1) == ["fodder quantity cattle"]


def test_option_lists_are_pruned_by_message_words():
    full = C.commodity_options()
    pruned = C.commodity_options("what is the onion price in junagadh today")
    assert "Onion" in pruned and "other_named_in_message" in pruned and len(pruned) < 12 < len(full)
    assert "Onion Green" in pruned  # near-duplicates stay so Jev picks between them
    assert set(C.commodity_options("kem cho")) >= {"Onion", "Wheat", "Cotton", "none"}  # common fallback
    d = C.district_options("cotton rate in junagadh")
    assert "Junagadh" in d and len(d) <= 4
    assert "Banaskantha" in C.district_options("prices at Deesa mandi")  # town -> district
    assert len(C.district_options("how are you")) == 34  # no hit -> full table


def test_closed_sets_have_escapes():
    assert "not_stated" in C.district_options() and "Junagadh" in C.district_options()
    assert set(C.scheme_options()) >= {"kcc", "pmfby", "none"}
    assert "other_named_in_message" in C.commodity_options() and len(C.commodity_options()) <= 255
    assert C.deterministic_scheme_code("પાક વીમો") == "pmfby"
