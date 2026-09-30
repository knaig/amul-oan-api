# Changes in this fork vs upstream OpenAgriNet/amul-oan-api

- **Fork:** `knaig/amul-oan-api`, branch `main` (2026-09-30)
- **Upstream:** `OpenAgriNet/amul-oan-api`, branch `main`
- **Forked from:** `1c618e8` (2026-09-21, "Merge pull request #301 from OpenAgriNet/feat/chat-vet-office-lookup")
- **Fork adds:** 24 commits, ~52 files (vs the fork point; regenerate below for exact numbers)
- **Upstream has moved on:** 42 commits on upstream `main` are not in this fork

Regenerate the numbers with:

```bash
MB=$(git merge-base fork/main origin/main)
git log --oneline origin/main..fork/main
git diff --stat $MB fork/main
git rev-list --count fork/main..origin/main
```

## 1. What the fork is

One feature: an alternative "agent step" for the chat turn. Upstream answers a farmer
message with a pydantic-ai tool loop, which is usually 2 LLM requests: one to pick tools,
one to write the answer. The fork adds a second path:

1. One TypeSafe Jev request answers ~25 closed-set questions about the message (intent,
   tool, slots, dialogue state, optionally the safety check).
2. Code (`app/planner/decode.py`) turns those answers into tool calls.
3. The tools run directly, in parallel.
4. One LLM request writes the answer from the results.

This path is off by default (`PLANNER_MODE=llm`). Around it are a side-by-side lab UI,
a trace store and several latency options, which are also off by default. Full design and
measurements: `docs/JEV_PLANNER.md`.

## 2. Commits (oldest first)

| commit | summary |
|---|---|
| `5c0ebdf` | Jev (TypeSafe) one-call agent step with side-by-side lab |
| `2f072d3` | lab: English query/answer capture, sample picker, flow-labelled dashboard |
| `fecdcd0` | planner: health call only on booking-shaped routes; per-sample farmer in eval |
| `62fc350` | docs: 14-question rerun results |
| `83570ee` | lab: one-click hands-off demo run with per-turn A vs B summary |
| `80f7f5d` | lab: keep previous turn on screen between demo turns |
| `81d899f` | lab: stage-median rows explain what total turn contains |
| `a25ffb5` | lab: plain-language Simple view with dual timelines and savings callout |
| `1f6392c` | lab: farmer-profile and answer-translation stages, warm endpoint, sequential Simple view |
| `b3e1c26` | chat: optional voice-style concurrent moderation with a token gate |
| `9401e02` | chat: concurrent moderation verdict as plain object; record decline as answer |
| `63b5633` | lab: planner-relevant time, server pipeline + env template |
| `c777111` | test: pin sequential moderation in the suite; docs: relevant time and server config |
| `bca24ad` | fast prep-model profile, pipelined answer translation, test-mode wording |
| `c286028` | Jev safety check inside the plan request; long HTTP keep-alive + Jev warm ping |
| `47e22b6` | planner: strict-format compose notes for bonus/milk; tests pinned against lab .env |
| `6323dfb` | test: pin health flags against the lab .env |
| `bfdd004` | docs: latency work from the lab |
| `9409298` | lab: backend-speed presets for the stand-in |
| `5f24d7f` | planner: identifiers verbatim rule + ticket safety net on the Jev arm |
| `8ee70cf` | lab: question-language switch on the Simple view, English default |
| `961671b` | planner review fixes: real confidence gate, two-signal writes, enforced loop limit |
| `b3edfae` | docs: this file |
| (next) | keep negation in search keywords, override flag off in production, Jev client on httpx2 with a retry budget, lab scripts committed |

## 3. Changes to upstream files

Each change is tagged by its effect on a default deployment (no planner env vars set):

- **ALWAYS ON:** changes behaviour for every turn.
- **IDENTITY:** code path added, but does nothing unless a planner flag or the lab turns it on.
- **FLAG:** only active when the named env var is set.

| file | +/- | change | default effect |
|---|---|---|---|
| `app/services/chat.py` | +384 / -91 | Picks the arm per turn (`ChatRequest.planner` or `PLANNER_MODE`). Adds stage timers, shadow hook, trace row. Adds the Jev arm, early planning overlapped with moderation, Jev-as-moderation with LLM fallback, concurrent moderation with a token gate, and pipelined answer translation. Existing translation calls are wrapped in a timer. | IDENTITY for `llm` mode with flags off; the sequential path is kept. See the caution on `PLANNER_OVERRIDE_ENABLED` in section 6. |
| `app/llm_core/execution.py` | +72 / -4 | `stream()` gains an optional `observer` that times model requests and tool calls. `context()` accepts a forced `profile_name`. **`agent.iter` now receives `UsageLimits(request_limit=...)` read from the agent's `model_settings`.** | **ALWAYS ON:** the legacy loop now stops at 10 requests (agrinet) or the doctor limit. Upstream silently allowed pydantic-ai's default of 50, because `request_limit` inside `ModelSettings` is ignored. A turn that hits the limit ends in an error instead of looping. Observer and profile: IDENTITY. |
| `app/llm_core/factory.py` | +15 | httpx keep-alive expiry raised from 5 s to 600 s (32/128 connections). Request hook that counts tokens when a lab sink is set. | **ALWAYS ON:** longer-lived connections to model providers (fewer TLS handshakes). Token hook: IDENTITY. |
| `agents/tools/loan.py` | +8 | Dry-run switch. **`confirmed=True` now waits on `ensure_in_scope()`** (moderation verdict) before issuing a code / SMS. | **ALWAYS ON**, but a no-op unless a concurrent moderation task is attached (voice path, or `PLANNER_CONCURRENT_MODERATION`). Dry-run: IDENTITY. |
| `agents/tools/ai_call.py`, `agents/tools/health_call.py` | +3 each | Dry-run switch (contextvar, default off). | IDENTITY |
| `agents/tools/search.py` | +2 | `top_k` override from a contextvar. | IDENTITY |
| `agents/agrinet.py` | +10 | `prepare_tools` hook that hides tools listed in a contextvar. | IDENTITY |
| `app/models/requests.py` | +4 | `ChatRequest.planner: 'llm' \| 'jev' \| None`. | New optional API field. |
| `app/routers/chat.py` | +1 | Passes `planner` through. | See section 6. |
| `main.py` | +17 / -1 | Mounts the `lab` router. Starts a 25 s Jev keep-alive ping when a TypeSafe key is set and the planner or lab can be used. | Lab returns 404 when `PLANNER_LAB_ENABLED` is off (default: off in production). Ping: FLAG (`TYPESAFE_API_KEY` + mode/lab). |
| `requirements.txt` | +4 | `typesafe-sdk>=0.7.1`, `aiosqlite>=0.20.0`. | New dependencies installed. |
| `example.env` | +23 | Documents every `PLANNER_*` / `TYPESAFE_*` variable. | none |
| `.gitignore` | +15 | Ignores `planner_traces.db*`, `.lab/`, `dump.rdb`; un-ignores the lab scripts under `scripts/` (upstream ignores `/scripts/*`). | none |
| `tests/conftest.py` | +14 | Pins planner and health flags so a local lab `.env` cannot change the suite. | tests only |

## 4. New files

| path | purpose |
|---|---|
| `app/planner/questions.py` | Turn state + the Jev question set; replicates the upstream `prepare=` tool gates in code |
| `app/planner/candidates.py` | Closed sets (districts, schemes, commodities, SHC cycles, periods), date regex, farmer-profile parsers |
| `app/planner/decode.py` | Jev answers -> tool plan; the agent prompt's routing rules restated in code; confidence gate |
| `app/planner/keywords.py` | Deterministic search-query shaping (replaces model-drafted keywords) |
| `app/planner/planner.py` | `plan_turn()`: one Jev request -> plan or escalation |
| `app/planner/jev.py` | TypeSafe SDK wrapper, long keep-alive client, warm ping |
| `app/planner/executor.py` | Runs planned tools directly and in parallel; dry-run for side effects |
| `app/planner/compose.py` | The single answer-writing call: same persona prompt, tool results in the user turn, no tools |
| `app/planner/arms.py` | `jev_agent_stream()` (drop-in for `execution.stream`), escalation, ticket safety net |
| `app/planner/gate.py` | Holds tokens until a concurrent moderation verdict arrives |
| `app/planner/streaming.py` | Pipelined answer translation (batches in order, 3 in flight) |
| `app/planner/shadow.py` | Shadow mode: Jev plans in the background on legacy turns, records agreement |
| `app/planner/tracestore.py` | `planner_turns` table (SQLite or Postgres) + `/stats` aggregates |
| `app/planner/side_effects.py` | Per-request contextvars: dry-run, disabled tools, top_k, token sink |
| `app/planner/config.py`, `models.py`, `__init__.py` | Settings from env, plan and trace types |
| `app/routers/lab.py` | `/api/lab/*`: side-by-side runs, plan preview, stats, ratings, warm, stand-in presets |
| `app/static/lab.html`, `lab_simple.html` | Lab UIs (full and plain-language) |
| `assets/commodities.json` | 199 Agmarknet commodity names for the mandi Choice |
| `pipeline.lab.yaml`, `pipeline.server.yaml`, `.env.server.example` | Model profiles for the lab and the on-prem "2-3 s" configuration |
| `docs/JEV_PLANNER.md` | Design, feasibility table, measurements, caveats, rollout |
| `tests/test_planner_decode.py`, `test_planner_pipeline.py`, `test_planner_candidates.py` | 59 planner tests |
| `scripts/beckn_standin.py`, `scripts/standin_data.py` | Beckn / ONIX bridge / PashuGPT stand-in on :3100 with synthetic farmers, animals, KB, schemes, mandi, weather. **Scheme and KB texts are made-up fixture data tagged with real source names; do not quote them as facts.** |
| `scripts/lab_up.sh`, `scripts/lab_down.sh` | Start / stop Redis + stand-in + app for the lab |
| `scripts/planner_eval.py`, `planner_report.py`, `lab_multiturn.py`, `planner_eval_sample.jsonl` | Batch A/B eval, report, multi-turn scenarios, the 14 sample questions |

## 5. Configuration added

All default to upstream behaviour.

| variable | default | effect |
|---|---|---|
| `PLANNER_MODE` | `llm` | `llm` = upstream loop; `jev` = Jev arm; `shadow` = upstream answers, Jev plans in the background |
| `TYPESAFE_API_KEY`, `TYPESAFE_MODEL`, `TYPESAFE_TIMEOUT_S` | unset, `jev-latest`, `8` | Jev access |
| `PLANNER_OVERRIDE_ENABLED` | on only when `ENVIRONMENT != production` | honour `ChatRequest.planner` per request |
| `PLANNER_TOOL_MIN_CONFIDENCE`, `PLANNER_ARG_MIN_CONFIDENCE`, `PLANNER_YES_THRESHOLD`, `PLANNER_EXTRA_TOOL_THRESHOLD` | 0.45, 0.40, 0.60, 0.70 | decoder thresholds |
| `PLANNER_LOW_CONFIDENCE_POLICY` | `escalate_llm` | what a low-confidence plan does |
| `PLANNER_SEARCH_TOP_K`, `PLANNER_SEARCH_FANOUT`, `PLANNER_MILK_DEFAULT_RANGE_DAYS`, `PLANNER_HISTORY_PAIRS` | 8, 2, 7, 3 | retrieval and state shaping |
| `PLANNER_CONCURRENT_MODERATION` | `false` | moderation overlaps the agent step (both arms) |
| `PLANNER_PIPELINED_TRANSLATION` | `false` | translate answer batches during generation (both arms) |
| `PLANNER_MODERATION_SOURCE`, `PLANNER_MODERATION_MIN_CONFIDENCE`, `PLANNER_MODERATION_COMPARE` | `llm`, 0.60, `false` | Jev as the safety check |
| `PLANNER_DRY_RUN_SIDE_EFFECTS`, `PLANNER_DISABLED_TOOLS` | `false`, empty | lab safety switches (never on in production) |
| `PLANNER_LAB_ENABLED`, `PLANNER_TRACE_DB_URL` | off in production, SQLite | lab UI and trace store |

## 6. Things to know before merging upstream or deploying

1. **Two changes are live even with the planner off:** the enforced request limit on the
   legacy loop (`execution.py`), and the 600 s provider keep-alive (`factory.py`). The loan
   moderation wait is also always on but inert without a moderation task. Each is small
   and arguably a fix, but they are production behaviour changes and belong in their own
   upstream PR, separate from the planner.
2. **`PLANNER_OVERRIDE_ENABLED`** now defaults to off in production (it was `true`, which let
   any client choose the Jev arm per request). `.env.server.example` still turns it on for a
   lab server; drop that line for a production deployment.
3. **Upstream is 42 commits ahead.** `app/services/chat.py` is the most likely conflict
   (+384 / -91 here). Rebase or merge upstream `main` before any PR.
4. **Lab scripts are now in the fork.** They were on disk but never committed, because
   upstream ignores `/scripts/*`; the fork's `.gitignore` now un-ignores them by name. An
   upstream PR would have to decide whether they belong in `scripts/`.
5. **Accuracy and cost figures are not yet reliable.** They were measured on the questions
   the decoder was tuned on, before the loop-limit fix, and without prompt-caching
   discounts. See the caveats in `docs/JEV_PLANNER.md` section 4b.
6. **Test status:** 1351 pass, 2 skipped. 5 failures (glossary parity for bn/mr/pa, the Hindi pipeline,
   the Beckn AI technician mapper) fail identically before and after the fork's last commit,
   and none are in files the fork touches.
