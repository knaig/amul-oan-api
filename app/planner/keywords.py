"""Deterministic search-query shaping (the part of the old first call that
'drafted' retrieval keywords). Jev does not generate text, so the query is the
farmer's own English words compacted to keywords, plus code-owned expansions."""
from __future__ import annotations

import re

_STOPWORDS = set("""a an the and or but of for to in on at by with from about into over after before is are was
were be been being am do does did done have has had having what which who whom whose how when where why
my me mine i we our ours us you your yours it its this that these those there here please tell give show
get want wants need needs know can could would should will shall may might much many some any also just
very really so than then too as if because while whether not no yes ok okay hi hello namaste sarlaben
sarla ben amul ai assistant question ask asking told say said sir madam bhai ji""".split())

_TOPIC_EXPANSION = {
    "clinical": ["symptoms", "treatment"],
    "nutrition": ["feed", "ration"],
    "breeding": ["heat", "insemination"],
    "crop": ["cultivation", "management"],
    "cattle_trade": ["Amul cattle trade", "buy sell"],
    "scheme": ["scheme", "benefit"],
}

_SPECIES_WORDS = ("cow", "cows", "buffalo", "buffaloes", "buffalos", "calf", "calves", "goat", "goats",
                  "sheep", "poultry", "hen", "chicken", "bull", "bullock", "heifer", "cattle", "animal", "animals")


def keywordize(text: str, max_tokens: int = 12) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-]*|\d+", text or "")
    kept: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS or low in seen or len(low) < 2:
            continue
        seen.add(low)
        kept.append(low)
        if len(kept) >= max_tokens:
            break
    return " ".join(kept)


def mentions_species(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(rf"\b{w}\b", low) for w in _SPECIES_WORDS)


def search_queries(text: str, *, topic: str, fanout: int, livestock_default: bool = True) -> list[str]:
    """1..fanout keyword queries. Q1 = compacted farmer words (+ 'cattle' when the
    farmer named no animal and the topic is livestock, per the species rule).
    Q2 = Q1 + topic expansion. Q3 = Q1 + 'dairy cow buffalo'."""
    base = keywordize(text)
    if not base:
        base = keywordize(topic) or "dairy animal care"
    if livestock_default and topic in ("clinical", "nutrition", "breeding") and not mentions_species(text):
        base = f"{base} cattle"
    queries = [base]
    expansion = _TOPIC_EXPANSION.get(topic)
    if fanout >= 2 and expansion:
        queries.append(" ".join([base, *[e for e in expansion if e not in base]]))
    if fanout >= 3:
        queries.append(f"{base} dairy cow buffalo")
    # The Beckn search validator rejects > 20 tokens.
    return [" ".join(q.split()[:18]) for q in queries[:max(1, fanout)]]
