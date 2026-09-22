"""Closed sets and code-side candidate extraction for the planner.

Jev selects; code proposes. Everything here is deterministic: the district table,
the 15 central scheme codes, the Agmarknet commodity list, the SHC cycles, the
relative date periods, and parsers that lift structured facts (accounts, AI
technicians, union ban) back out of the farmer-context markdown the prompt uses.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz

from agents.tools.districts import DISTRICTS, resolve_place
from agents.tools.scheme_codes import SCHEME_CODES, SCHEME_LABELS, resolve_scheme_code

_IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(_IST).date()


# ── Districts ────────────────────────────────────────────────────────────────

_PRUNE_STOP = {"price", "prices", "rate", "rates", "today", "market", "mandi", "bhav", "what", "which", "much", "cost", "near", "nearby", "district", "village", "town", "area", "tell", "give", "show", "want", "know", "please", "farmer", "weather", "rain", "forecast"}


def _message_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or "") if w.lower() not in _PRUNE_STOP]


def _fuzzy_hit(name: str, words: list[str], cutoff: int) -> bool:
    """True when any message word resembles any word of the option name."""
    parts = [p for p in re.findall(r"[A-Za-z]{3,}", name) if p.lower() not in ("seed", "leaves", "whole", "dry", "green")]
    return any(fuzz.ratio(w.lower(), p.lower()) >= cutoff for w in words for p in parts) or any(
        fuzz.partial_ratio(w.lower(), name.lower()) >= 90 for w in words if len(w) >= 4)


def district_options(query: Optional[str] = None) -> dict[str, str]:
    """Choice criteria: district display -> description, plus not_stated.

    When a query is given, the list is pruned to districts (or their towns) that
    resemble a word of the message: fewer distractors and fewer tokens (Jev
    accuracy falls with state size). No match -> the full 33 (still small)."""
    locs = list(DISTRICTS.values())
    if query:
        words = _message_words(query)
        hits = [loc for loc in locs if _fuzzy_hit(loc.display, words, 80)
                or any(_fuzzy_hit(c.town, words, 85) for c in loc.candidates)]
        if hits:
            locs = hits
    opts = {loc.display: f"the farmer names {loc.display} district or a town in it" for loc in locs}
    opts["not_stated"] = "the farmer names no place at all in this message"
    return opts


def district_display_to_key(display: str) -> Optional[str]:
    resolved = resolve_place(display)
    return resolved.key if resolved else None


# ── Central schemes ──────────────────────────────────────────────────────────

def scheme_options() -> dict[str, str]:
    opts = {code: SCHEME_LABELS[code] for code in SCHEME_CODES}
    opts["none"] = "no central / national government scheme is named or implied"
    return opts


def deterministic_scheme_code(text: str) -> Optional[str]:
    return resolve_scheme_code(text)


# ── Commodities ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def commodity_list() -> list[str]:
    path = Path(__file__).resolve().parents[2] / "assets" / "commodities.json"
    try:
        return list(json.loads(path.read_text(encoding="utf-8"))["commodities"])
    except Exception:
        return ["Onion", "Potato", "Tomato", "Wheat", "Cotton", "Groundnut"]


_COMMON_COMMODITIES = ("Onion", "Potato", "Tomato", "Wheat", "Cotton", "Groundnut", "Castor Seed", "Cummin Seed(Cumin Seed)",
                       "Bajra(Pearl Millet/Cumbu)", "Maize", "Paddy(Dhan)(Common)", "Rice", "Soyabean", "Mustard", "Garlic", "Green Chilli",
                       "Banana", "Mango", "Brinjal", "Cabbage", "Cauliflower", "Bhindi(Ladies Finger)", "Sugarcane", "Jowar(Sorghum)",
                       "Green Gram (Moong)(Whole)", "Bengal Gram(Gram)(Whole)", "Arhar (Tur/Red Gram)(Whole)", "Fennel Seeds (Saunf)",
                       "Isabgul (Psyllium)", "Sesamum(Sesame,Gingelly,Til)", "Turmeric", "Ginger(Green)", "Lemon", "Papaya", "Pomegranate")


def commodity_options(query: Optional[str] = None) -> dict[str, Optional[str]]:
    """Agmarknet names pruned to those resembling a message word (pre-parsed
    value extraction), else a compact common list. Escapes always present, so an
    unlisted crop is still recoverable from the message span."""
    names = commodity_list()
    if query:
        words = _message_words(query)
        hits = [n for n in names if _fuzzy_hit(n, words, 80)]
        names = hits if hits else [n for n in _COMMON_COMMODITIES if n in names] or names[:40]
    # Jev caps a Choice at 255 options; keep headroom for the two escapes.
    opts: dict[str, Optional[str]] = {name: None for name in names[:250]}
    opts["other_named_in_message"] = "the farmer names a crop or commodity that is NOT in this list"
    opts["none"] = "no crop or commodity is named"
    return opts


# ── Spans (pre-parsed value extraction) ──────────────────────────────────────

_STOP = set("""a an the and or of for to in on at by with from about is are was were be been am do does did
what which who whom whose how when where why my me i we our us you your it its this that these those
please tell give show get want need know can could would should will shall may might much many some any
price prices rate rates today yesterday tomorrow now current latest market mandi bhav weather forecast rain
kg quintal per near nearby area district village town city""".split())


def noun_spans(text: str, max_spans: int = 24) -> list[str]:
    """Candidate 1-2 word spans from the English query, for 'pick the span' questions."""
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z\-]+", text or "")]
    spans: list[str] = []
    seen: set[str] = set()
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in _STOP or len(low) < 3:
            continue
        for span in (tok, " ".join(tokens[i:i + 2]) if i + 1 < len(tokens) else None):
            if not span:
                continue
            key = span.lower()
            if key in seen or any(w.lower() in _STOP for w in span.split()) and span == tok:
                continue
            seen.add(key)
            spans.append(span)
            if len(spans) >= max_spans:
                return spans
    return spans


# ── Dates / periods ──────────────────────────────────────────────────────────

PERIODS = {
    "today": "only today",
    "yesterday": "only yesterday",
    "last_7_days": "the past week / last 7 days / recent days",
    "last_15_days": "the past fortnight / last 15 days",
    "this_month": "the current calendar month",
    "last_month": "the previous calendar month",
    "last_30_days": "the last 30 days / one month",
    "explicit_dates": "specific calendar dates or a date range are written in the message",
    "not_stated": "no time period is mentioned",
}

_DATE_PATTERNS = [
    (re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b"), ("d", "m", "y")),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s+(20\d{2}))?", re.I), ("d", "mon", "y?")),
    (re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?", re.I), ("mon", "d", "y?")),
]
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def explicit_dates(text: str, today: Optional[date] = None) -> list[date]:
    """All absolute dates written in the text, in order of appearance (code, not Jev)."""
    today = today or today_ist()
    found: list[tuple[int, date]] = []
    for pattern, shape in _DATE_PATTERNS:
        for m in pattern.finditer(text or ""):
            groups = m.groups()
            try:
                parts = dict(zip(shape, groups))
                year = int(parts.get("y") or parts.get("y?") or today.year)
                if "mon" in parts:
                    month = _MONTHS[parts["mon"][:3].lower()]
                else:
                    month = int(parts["m"])
                day = int(parts["d"])
                d = date(year, month, day)
                if d > today and ("y?" in parts and not parts.get("y?")):
                    d = date(year - 1, month, day)
                found.append((m.start(), d))
            except (ValueError, KeyError):
                continue
    found.sort()
    out: list[date] = []
    for _, d in found:
        if d not in out:
            out.append(d)
    return out


def period_to_range(period: str, text: str, *, default_days: int, max_days: int = 31, today: Optional[date] = None) -> tuple[date, date]:
    today = today or today_ist()
    if period == "explicit_dates":
        ds = explicit_dates(text, today)
        if len(ds) >= 2:
            start, end = min(ds[:2]), max(ds[:2])
        elif len(ds) == 1:
            start = end = ds[0]
        else:
            start, end = today - timedelta(days=default_days), today
    elif period == "today":
        start = end = today
    elif period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "last_7_days":
        start, end = today - timedelta(days=7), today
    elif period == "last_15_days":
        start, end = today - timedelta(days=15), today
    elif period == "last_30_days":
        start, end = today - timedelta(days=30), today
    elif period == "this_month":
        start, end = today.replace(day=1), today
    elif period == "last_month":
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = end.replace(day=1)
    else:
        start, end = today - timedelta(days=default_days), today
    end = min(end, today)
    if (end - start).days > max_days:
        start = end - timedelta(days=max_days)
    return start, end


def shc_cycle_options(today: Optional[date] = None) -> dict[str, Optional[str]]:
    today = today or today_ist()
    start_year = today.year if today.month >= 4 else today.year - 1
    cycles = [f"{y}-{str(y + 1)[2:]}" for y in range(start_year - 2, start_year + 2)]
    opts: dict[str, Optional[str]] = {c: None for c in cycles}
    opts["not_stated"] = "no year or cycle is named"
    return opts


# ── Farmer context parsing (markdown produced by agents/farmer_context.py) ───

@dataclass(frozen=True)
class Account:
    index: int
    farmer_name: str
    union_code: str
    society_code: str
    farmer_code: str
    society_name: str
    union_name: str
    cows: int
    buffaloes: int

    def label(self) -> str:
        parts = [self.farmer_name or f"Farmer {self.index}"]
        if self.society_name:
            parts.append(self.society_name)
        herd = []
        if self.cows:
            herd.append(f"{self.cows} cow(s)")
        if self.buffaloes:
            herd.append(f"{self.buffaloes} buffalo(es)")
        if herd:
            parts.append(", ".join(herd))
        return " / ".join(parts)


@dataclass(frozen=True)
class Technician:
    name: str
    mobile: str
    user_id: str


_FIELD_RE = re.compile(r"^- \*\*(?P<label>[^*]+):\*\*\s*(?P<value>.*)$")
_TECH_RE = re.compile(r"\*\*Name:\*\*\s*(?P<name>.+?)\s*\|\s*\*\*Mobile number:\*\*\s*(?P<mobile>[^|]+?)\s*\|\s*\*\*user_id:\*\*\s*(?P<uid>\S+)")


def parse_accounts(farmer_info: str) -> list[Account]:
    accounts: list[Account] = []
    current: dict[str, str] = {}
    index = 0

    def flush() -> None:
        if current:
            def num(key: str) -> int:
                try:
                    return int(float(current.get(key, "0") or 0))
                except ValueError:
                    return 0
            accounts.append(Account(
                index=index,
                farmer_name=current.get("Farmer name", ""),
                union_code=current.get("Union code", ""),
                society_code=current.get("Society code", ""),
                farmer_code=current.get("Farmer code", ""),
                society_name=current.get("Society name", ""),
                union_name=current.get("Union name", ""),
                cows=num("Total cows"),
                buffaloes=num("Total buffalo"),
            ))

    for line in (farmer_info or "").splitlines():
        if line.startswith("## Farmer "):
            flush()
            current = {}
            try:
                index = int(line.split()[-1])
            except ValueError:
                index += 1
            continue
        if line.startswith("### Animal") or line.startswith("### Available AI") or line.startswith("### AI call"):
            # herd/milk sections belong to the account; animal sections do not
            if line.startswith("### Animal"):
                pass
        m = _FIELD_RE.match(line.strip())
        if m and current is not None and not line.startswith("#"):
            label, value = m.group("label").strip(), m.group("value").strip()
            if label not in current:
                current[label] = value
    flush()
    return [a for a in accounts if a.farmer_code or a.farmer_name]


def parse_technicians(farmer_info: str) -> list[Technician]:
    out: list[Technician] = []
    seen: set[str] = set()
    for m in _TECH_RE.finditer(farmer_info or ""):
        uid = m.group("uid").strip().rstrip(".")
        if uid in seen:
            continue
        seen.add(uid)
        out.append(Technician(name=m.group("name").strip(), mobile=m.group("mobile").strip(), user_id=uid))
    return out


def ai_call_banned(farmer_info: str) -> bool:
    return "AI call booking is not allowed for this union" in (farmer_info or "")


def herd_species_default(accounts: list[Account]) -> Optional[str]:
    cows = sum(a.cows for a in accounts)
    buffaloes = sum(a.buffaloes for a in accounts)
    if cows and not buffaloes:
        return "cow"
    if buffaloes and not cows:
        return "buffalo"
    return None
