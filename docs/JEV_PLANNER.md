# Jev planner: collapsing the agent step to one generative call

**Repo:** amul-oan-api (the chat + voice-tool API). **Status:** implemented behind `PLANNER_MODE`
(default `llm` = unchanged production behaviour). Lab UI at `/api/lab/`.

## 1. What the turn does today

```
pre-translation (LLM, gu/hi/bn/mr/pa -> en)      <- separate step, unchanged
moderation      (LLM, structured category)       <- separate step, unchanged
agent step      (pydantic-ai loop):
   request #1  -> model reads 8k-token prompt + 12 tool schemas, emits tool calls (or text)
   tools run   -> Beckn vet KB, Vistaar mandi/weather/schemes, PashuGPT, loan Postgres ...
   request #2  -> model composes the English answer from tool results
   (#3..#10)   -> only when the model chains tools; request_limit=10, retries=5
post-translation (TranslateGemma, streaming)     <- unchanged
```

The two calls the request is about are request #1 and request #2. Everything else in the
pipeline is untouched by this change.

## 2. Feasibility: what request #1 actually does, and how each part is covered

Jev (TypeSafe System One) returns typed answers to closed-set questions: Choice, Noul
(yes/no probability), Score. It does not generate text. The table is the complete list of
work request #1 performs today, derived from the agent prompt
(`assets/prompts/agrinet_system_translation_pipeline.md`), the tool docstrings, the
`prepare=` hooks and the pydantic-ai loop semantics.

| # | Request #1 responsibility today | Jev path (`app/planner/`) | Coverage |
|---|---|---|---|
| 1 | Intent classification (12 intents in prompt) | `intent` Choice, 14 options incl. loan/greeting | full |
| 2 | Choose 0, 1 or several tools (parallel_tool_calls) | `primary_tool` Choice + `also_<tool>` Nouls for parallel extras | full |
| 3 | Respect hidden tools (`prepare=` gates: signed-in, supported union, loan flag, SHC flag) | `TurnGates.enabled_tools()` replicates every gate in code; hidden tools are not offered as options | full, deterministic |
| 4 | Fill enum / closed-set args: species, case_type, scheme_code, cycle, confirmed | Choice per slot with explicit `not_stated` escape | full |
| 5 | Fill location args (mandi, weather) | Choice over the 33-district table + `place_span` Choice over words of the message for unlisted towns (tool then says "not covered" exactly as today) | full |
| 6 | Fill commodity (English Agmarknet name) | Choice over `assets/commodities.json` (199 names) + `other_named_in_message` -> span from the message | full; list is editable |
| 7 | Date ranges (milk, mandi) incl. "last month", "15 to 20 August" | Choice over relative periods + regex for absolute dates; arithmetic in code (Jev is not a calculator, per TypeSafe's own jaggedness notes) | full |
| 8 | Read codes / technician user_id / herd from Farmer Profile | parsed from the profile markdown in code (`candidates.parse_accounts/parse_technicians`); technician picked by Choice over names shown to the farmer | full, more reliable than a model copying ids |
| 9 | Multi-turn dialogue state: "yes" to a health-call offer, technician reply, species reply, loan confirm, cycle reply | Nouls on `last_assistant_message` (offered health call / listed technicians / asked species / offered loan) + `farmer_says_yes/no`; last 3 pairs in state | full |
| 10 | Species defaulting rule (cattle unless another animal named) | `species_named` Choice; code appends "cattle" to search and picks herd default for bookings | full |
| 11 | Booking precedence rules (health call outranks retrieval; AI vs health never mixed; union ban; missing technician list) | code rules in `decode.py`, ordered exactly as the prompt | full |
| 12 | Loan two-step (offer, then confirmed=true only after explicit yes) | `loan_request` Choice + `last_assistant_offered_loan` Noul | full |
| 13 | Ask a clarification question when a slot is missing | plan carries `compose_notes` (e.g. "ask cow or buffalo", "list technicians A, B") and the single compose call asks it | full, still one call |
| 14 | Answer directly with no tool (profile facts, greeting, language switch, out of scope) | `none_answer_directly` route + notes | full |
| 15 | **Draft retrieval keywords for `search_documents`** ("Strict Query Planning Block": 2-8 keywords, up to 3 variants, reformulate once) | Jev cannot write text. Query = farmer's own English words compacted to keywords in code, + topic/species expansions, sent as 2-3 parallel variants (fan-out). Reformulation-on-weak-results is replaced by the parallel fan-out. | **partial: different mechanism, must be measured** (see 4) |
| 16 | Free-text `remark` for health call; `scheme_name` "in the user's own words" | pass the English message (truncated) | full (same information) |
| 17 | Chain tools across rounds inside one turn (request #3+) | Only observed chain in this prompt is search-then-reformulate (covered by fan-out) and health-call-then-advice (covered: booking + advice note). Anything else escalates. | partial by design |
| 18 | Retry on tool validation errors (retries=5) | Jev outputs are valid by construction; the search validator message becomes a tool result | full |
| 19 | Tool result -> "ask for X" (vet office needs taluka, mandi place not covered, SHC no card) | tool text goes to the compose call, which relays exactly like request #2 does today | full |
| 20 | Moderation-gated side effects (`ensure_in_scope`), idempotency guards, account ownership checks | inside the tools; unchanged | full |
| 21 | Doctor persona (search only, up to 10 requests for completeness) | `needs_search` Noul + topic; fan-out 3 queries in parallel | partial: no iterative deepening |

Verdict: **feasible.** 18 of 21 responsibilities map one-to-one onto closed-set judgements
or code that already existed implicitly. The three partial items (15, 17, 21) are exactly
the places where request #1 *generated text or reasoned over its own tool output*, which is
the thing a System One model does not do. They are handled by parallel fan-out plus an
**accuracy floor**:

* **Confidence gate.** When the route is decided only by Jev's `primary_tool`/`intent`
  answer and its confidence is below `PLANNER_TOOL_MIN_CONFIDENCE`, the turn is handed to
  the legacy two-request loop (`low_confidence_policy=escalate_llm`). Worst case for an
  ambiguous message is therefore today's behaviour and today's latency. Slot-decided routes
  (a confirmed booking, an alias-matched scheme) are exempt because they are deterministic.
* **Jev unavailable** (no key, 429, timeout): same escalation. The farmer never sees an
  error caused by the planner.
* **Shadow mode** (`PLANNER_MODE=shadow`): production keeps answering with the legacy loop;
  Jev plans in the background and the tool-plan agreement is written to the trace store.
  This is how "no negative impact on accuracy" is verified on real traffic before flipping.

## 3. Architecture

```
app/planner/
  questions.py   turn state (compact) + speculative fan-out questions (all parallel, one request)
  candidates.py  closed sets (districts, schemes, commodities, cycles, periods), regex dates,
                 profile parsers (accounts, technicians, union ban)
  keywords.py    deterministic search-query shaping
  decode.py      answers -> Plan (the prompt's routing rules, in code)
  planner.py     plan_turn(): one Jev request -> Plan | escalate
  executor.py    runs the plan's tools directly, in parallel; dry-run switch for side effects
  compose.py     the ONE generative call: same persona prompt + "tool results" block, no tools
  arms.py        jev_agent_stream() drop-in for execution.stream(); legacy tool-call extraction
  shadow.py      background plan + agreement on legacy turns
  tracestore.py  per-(turn, arm) rows: stages, tools, plan, TTFT, totals, ratings
  side_effects.py contextvars: dry-run, disabled tools, top_k (honoured by BOTH arms)
app/routers/lab.py + app/static/lab.html   the side-by-side lab
```

Touch points in existing code (all default-off / identity):

* `app/services/chat.py`: picks the arm (`ChatRequest.planner` when `PLANNER_OVERRIDE_ENABLED`,
  else `PLANNER_MODE`), stage timers, shadow hook, trace row.
* `app/llm_core/execution.py::stream`: optional `observer` that times each model request and
  tool call of the legacy loop (needed to attribute request #1 vs #2 latency).
* `agents/agrinet.py`: `prepare_tools` honouring the disabled-tools contextvar.
* `agents/tools/search.py`: top_k override contextvar.
* `create_ai_call`, `create_health_call`, `check_loan_eligibility`: dry-run contextvar
  (lab default; never set in production).

Data sources are the real ones: the Jev executor calls the same tool functions the
pydantic-ai loop calls (Beckn vet-KB discovery, Bharat Vistaar seeker/BAP, PashuGPT bonus,
loan Postgres, Redis-backed farmer cache). There are no mocks in either arm.

## 4. Measuring latency and accuracy

Every turn in `jev`/`shadow` mode, and every lab turn, writes one row per arm to
`planner_turns` (`PLANNER_TRACE_DB_URL`; SQLite by default, Postgres recommended):

* `stages_json.spans`: pretranslation, moderation, `model_request_1..n` (legacy), `plan`,
  `tools`, `compose` (jev); `stages_json.tools`: per-tool ms and args.
* `ttft_ms`: agent-step start -> first token to the client (the number a voice bot feels).
* `total_ms`, `model_requests`, `jev_ms`, `jev_input_tokens`, `escalated`.
* `agreement` (shadow): `same | subset | different` tool set vs the legacy loop.
* `rating`: manual `correct | wrong | better | worse` from the lab.

`GET /api/lab/stats` returns p50/p95 per arm, agreement counts and rating counts. Langfuse
traces continue to work for both arms (compose runs through the same `execution.stream`).

Expected shape of the result (to be confirmed with keys): request #1 today is a full
8k-token prompt + 12 tool schemas on gpt-4.1 (typically 1.5-4 s before tools start); one
Jev request over a ~1-2k-token state with ~25 questions is billed on input tokens only
($0.042/Mtok) and TypeSafe reports it as sub-second. The lab measures the real delta.

Where accuracy could move, and what to watch in the lab/shadow data:

1. Retrieval recall (item 15): compare `search_documents` args across arms; rate answers.
2. Ambiguous short messages: watch `escalated` rate; tune `PLANNER_TOOL_MIN_CONFIDENCE`.
3. Non-English state: Jev is English-first; the planner always receives the pre-translated
   English query (as request #1 does today).
4. Jev "literal reading": every question states its escape option explicitly; if a class of
   message misroutes, fix the wording in `questions.py`, not the threshold.

## 4b. Measured (laptop, stand-in network, gpt-4.1 on both arms, 2026-09-22)

Ten Gujarati queries (`scripts/planner_eval_sample.jsonl`), gu -> en -> gu, side by side,
after the moderation/plan overlap and option-list pruning landed:

| arm | tool-set accuracy | TTFT p50 | TTFT p95 | total p50 | total p95 | model requests / turn |
|---|---|---|---|---|---|---|
| llm (gpt-4.1 x2) | 9/10 | 3.7 s | 11.1 s | 7.8 s | 28.6 s | 1.78 |
| jev (Jev + gpt-4.1 x1) | 10/10 | 3.6 s | 9.5 s | 7.1 s | 27.2 s | 1.00 |

Tool-plan agreement 9/10; the one disagreement is gpt-4.1 skipping retrieval on a sick-cow
message (it only offered a health call) where Jev searched and then offered. Median agent-step
TTFT saved by Jev: 0.57 s (mean 0.60 s). Jev plan itself: 0.35 to 0.5 s warm on ~5.7k input
tokens, and it now runs concurrently with moderation, so its critical-path cost is ~0.

Four multi-turn scenarios (health-call offer then "yes"; insemination then technician pick;
mandi with sticky place then "and cotton?"; milk then "and my bonus"): both arms correct on
every turn; Jev 0.4 to 1.1 s faster per turn (e.g. "yes please book it": 0.79 s vs 1.93 s).

Defects found by the lab and fixed the same day (all covered by tests): an insemination
request that Jev's health question read as "book a visit" (precedence rule), "Cotton (Kapas)"
chosen over "Cotton" (prefer the word the farmer used), a speculative low-confidence scheme
answer triggering a search on a bonus question (scheme route now needs a confident answer).

Same ten queries with **gpt-4.1-mini** as the generative model on both arms (the low-cost
option): the two-request loop drops to 8/10 tool-set accuracy (it also failed to book the
collapsed-buffalo health call, asking instead); the Jev arm stays 10/10 because routing no
longer depends on the compose model. TTFT p50 3.9 s both arms, median agent-step saving 0.57 s.
This is the cost story: Jev + a cheap compose model keeps routing quality that the cheap model
alone loses.

**Cost (agent step only, tiktoken estimate of the exact request bodies, list prices Sept 2026):**

| generative model | Flow A tokens in / out per turn | Flow B tokens in / out (+ Jev) | Flow A $/turn | Flow B $/turn |
|---|---|---|---|---|
| gpt-4.1 | 17.9k / 301 | 7.6k / 296 (+5.6k Jev) | $0.0383 | $0.0178 |
| gpt-4.1-mini | 17.9k / 95 | 7.6k / 302 (+5.6k Jev) | $0.0073 | $0.0038 |

Flow A pays for the ~9k-token prompt + tool schemas twice; Flow B pays for it once and buys
the plan from Jev at $0.042 per million tokens. Across four runs of the ten queries Flow B
was 10/10 on tool set every time; Flow A 8/10 to 9/10. Agent-step TTFT medians moved 0.1 to
0.6 s in Flow B's favour per run; the tail (milk-records query, several Beckn round trips)
is dominated by tool latency and hit either arm at random (5 to 26 s).

Caveats: n=10 per run on a laptop over the public internet; tool latencies are stand-in
constants; ratings not yet collected. Re-run `scripts/planner_eval.py` with a larger set and
real backends before quoting numbers externally.

## 5. Running

### 5a. Laptop: real pipeline, stand-in network (no production credentials)

`scripts/beckn_standin.py` speaks the seeker, ONIX transaction-bridge and PashuGPT
contracts (see its docstring) with seeded, real-shaped data: three farmers (single
cow account; two Banas accounts with operated visits; a Kutch farmer whose union bans AI
calls), animals, technicians, milk rows, bonus, a Soil Health Card, 28 vet-KB advisories,
union and central schemes, mandi rows per market and a 5-day forecast. Each hop has a
configurable latency (defaults from measured production numbers). The planner, tools,
Redis cache, callback correlation store and callback ingress are the production code.
Tool backends run identically in both arms, so their latency cancels out of the delta.

```bash
cp example.env .env   # or use the prepared lab .env: keys + stand-in URLs + pipeline.lab.yaml
./scripts/lab_up.sh   # redis, stand-in :3100, app :8000 -> http://127.0.0.1:8000/api/lab/
./scripts/lab_down.sh
```

`pipeline.lab.yaml` defines model profiles (`gpt41` baseline, `gpt41mini`,
`gemini_flash`, `openrouter_cheap`); pick one per turn in the lab ("Model profile") or
`--profile` in the batch script. Same profile on both arms = the latency claim; a cheap
profile on the Jev arm only = the cost claim.

Test farmer mobiles: `9876543210` (Kaira, cows, technicians, SHC 2024-25),
`9898989898` (Banas, two accounts, buffaloes, union schemes), `9700000001` (Kutch, AI
calls banned).

### 5b. Against real backends

```bash
cp example.env .env                    # fill OPENAI_API_KEY, BECKN_*, VISTAAR_*, REDIS_*, TYPESAFE_API_KEY
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn main:app --reload    # ENVIRONMENT=development enables /api/lab
open http://localhost:8000/api/lab/
```

* **Send message**: runs the chosen arms concurrently on separate conversation histories
  (`<id>::llm`, `<id>::jev`), streams both answers, shows tools, stage waterfall, TTFT delta
  and tool-plan agreement. Rate each answer; ratings persist.
* **Start call (mic)**: records audio, Bhashini STT, both arms, Bhashini TTS per answer
  (needs `BHASHINI_*`). This emulates a voice-bot turn; the telephony voice service itself
  lives in a separate (private) repo and is not part of this change.
* **Preview Jev plan only**: state, questions, probabilities and the decoded plan with no
  tools executed. Use it to tune wording and thresholds.
* **Adjust**: enable/disable any tool for both arms, RAG top_k, search fan-out, thresholds,
  low-confidence policy, Jev model, history depth, dry-run of side effects.
* Per-turn A/B from any client: `GET /api/chat/?...&planner=jev`.
* Batch: `python scripts/planner_eval.py queries.jsonl` (see script header).

Production rollout: `PLANNER_MODE=shadow` first (agreement rate, zero farmer impact), then
`PLANNER_MODE=jev` with `escalate_llm` as the floor.

## 6. Known limits (stated up front)

* Search queries are the farmer's words compacted, not model-drafted keywords. Whether that
  hurts or helps recall on the Beckn vet KB is an empirical question the lab answers.
* Iterative tool chaining inside one turn is not planned by Jev; those turns escalate.
* Commodity and district lists are closed sets by design (the tools already reject anything
  else). New names go in `assets/commodities.json` / `agents/tools/districts.py`.
* Jev accuracy on non-English text is lower; the planner only ever sees English.
