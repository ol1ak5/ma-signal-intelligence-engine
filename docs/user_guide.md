# 📘 M&A Signal Intelligence Engine — User Guide

A multi-agent system that scans recent M&A news, structures it into signals,
profiles potential buyers, scores them deterministically against an acquisition
target, and produces a tiered outreach strategy.

It is built on the **Google Agent Development Kit (ADK)** with **Gemini**, and
runs from the terminal via a single entrypoint, `run.py` (no `adk web` needed).

---

## 1. What it does (the pipeline)

A request flows through five agents in sequence (an ADK `SequentialAgent`;
buyer profiling additionally runs inside an ADK `LoopAgent` that retries it
once if a flaky model turn returns empty output):

| # | Agent | Role | LLM? |
|---|-------|------|------|
| 1 | `news_ingestion_agent` | Searches the web (Google Search grounding) for recent M&A deals related to the target | Yes |
| 2 | `signal_extraction_agent` | Turns raw news into structured signal objects (buyer, sector, multiple, urgency…) | Yes |
| 3 | `buyer_profiling_agent` | Researches each buyer and emits structured **JSON** profiles (sector/geo focus, financial capacity, deal-size range) | Yes |
| 4 | `deal_matching_agent` | **Pure Python** — scores & ranks buyers vs. the target, applies hard exclusions | No |
| 5 | `strategy_agent` | Classifies ranked buyers into Tier 1/2/3 and writes an outreach strategy | Yes |

Supporting modules: `tools/` (ECB currency conversion, deterministic buyer
scoring), `skills/` (anti-hallucination rules), `memory/` (precedent-deal store),
`observability/` (a tracing plugin + pipeline metrics).

**Context compaction:** the orchestrator's `App` also enables context
compaction via a **deterministic, no-API summarizer** (`orchestrator/compaction.py`).
As events accumulate during a run, it condenses older ones into a single event —
preserving each agent's text while truncating bulky tool output — to keep the
context focused. It is intentionally LLM-free so it adds no model calls (unlike
ADK's default summarizer).

---

## 2. Prerequisites

| Tool | Version used | Why you need it |
|------|--------------|-----------------|
| **Python 3** | 3.11.5 | Runs the whole project. Use `python3` (not `python`) on macOS. |
| **pip** | 26.1.2 | Installs the Python packages below. |
| **uv** *(optional)* | 0.11.21 | Faster installer/runner used in development. Not required — `pip` works. |
| **adk** (CLI) | 2.2.0 | Comes bundled with `google-adk`; useful for interactive poking, but **not** how we run the pipeline. |

You also need a **Google AI Studio API key** (free) — see §4.

---

## 3. Dependencies (what each one is for)

Install everything with:

```bash
pip install -r requirements.txt
```

### Python packages (in `requirements.txt`)

| Package | Version | What it's for in this project |
|---------|---------|-------------------------------|
| **google-adk** | 2.2.0 | The agent framework — `Agent`, `SequentialAgent`, `App`, `Runner`, the plugin system (our tracer), and the built-in `google_search` tool. This is the backbone. |
| **google-genai** | 2.8.0 | The Gemini client. ADK uses it under the hood for every model call; we also use it directly for `types.Content` (building messages) and for the fast target-extraction call in `run.py`. |
| **requests** | 2.34.2 | Used by `tools/currency_tool.py` to fetch live EUR exchange rates from the European Central Bank XML feed. |
| **python-dotenv** | 1.2.2 | Loads your API key from `orchestrator/.env` into the environment at startup. |

Transitive dependencies (pydantic, google-auth, the Gemini HTTP stack, …) are
pulled in automatically by `google-adk` — you don't list them.

The standard library does the rest (`json`, `re`, `asyncio`, `uuid`,
`datetime`, `pathlib`, `xml.etree`, `argparse`, `logging`) — nothing to install.

---

## 4. Configuration (API key)

The engine authenticates to Gemini with an **AI Studio API key** (not Vertex AI).

1. Get a free key at <https://aistudio.google.com/apikey>.
2. Put it in **`orchestrator/.env`** (this is the single source of truth, and it
   is git-ignored so your key is never committed):

   ```bash
   GOOGLE_GENAI_USE_VERTEXAI=FALSE
   GOOGLE_API_KEY="your-key-here"
   ```

### Models used

| Purpose | Model |
|---------|-------|
| Target extraction (parsing your request → structured target) | `gemini-2.5-flash-lite` (fast & cheap) |
| All 5 pipeline agents | `gemini-2.5-flash` |

### ⚠️ Free tier is NOT enough for a full run — billing required

The Gemini **free tier allows only 5 requests per minute** (plus a small daily
quota) for `gemini-2.5-flash`, **per Google Cloud project** (not per key). A
full pipeline run makes **~15–25 model calls** (five agents plus the nested
web-search calls fired by buyer profiling), so a free-tier run will hit
`429 RESOURCE_EXHAUSTED` before it finishes — every time, not occasionally.

To run the whole pipeline end to end, **enable pay-as-you-go billing** on your
AI Studio project. The cost is small — a complete run is on the order of a few
cents with Gemini Flash. Swapping in a *new key* does **not** help — the caps
are per project. (The three offline evals need no API at all; see §8.)

---

## 5. How to launch the agent

Run from the project root. There are three ways:

### a) Custom request — interactive

```bash
python3 run.py
```
It prompts you:
```
Describe the acquisition target (or press Enter for the built-in example):
> Find buyers for a 500MW solar PV portfolio in Spain
```
Type your request in plain language, press Enter.

### b) Built-in example

Run `python3 run.py` and just **press Enter** at the prompt — it runs the
default target (a 500MW Solar PV Portfolio in Spain). No extra cost.

### ⚠️ Writing a built-in example - important

When describing the acquisition target, include as much detail as possible
(company name, sector, country, region, size, and any relevant characteristics).
More specific inputs help the system to identify the most relevant buyers and
reduce the likelihood of Gemini 503 "Service Unavailable" error.

### c) One-shot — quotes required

```bash
python3 run.py "Find buyers for a distressed German fiber-optic company"
```
> The request **must be in quotes**, or the shell splits it into separate
> arguments and only the first word reaches the program.

---

## 6. How to write a request

Write a natural-language description of the **asset being sold**. A fast lite
model extracts the structured fields the scorer needs, so include where you can:

- **What** the asset is (company / renewable portfolio / real estate / infrastructure)
- **Sector** — one of the 18 recognised sectors listed below
- **Country / region**
- **Size** *(optional)* — include only if you happen to know it; for an asset
  that's up for sale the value is usually unknown, and if omitted the size-fit
  part of the scoring is simply left neutral. For **renewable portfolios**,
  stating the capacity in MW (e.g. "500MW") lets the scorer size-match on MW
  (physical capacity) rather than price — the natural yardstick for that class.

### Target sectors

For the most reliable results, frame your target within one of these **18
sectors**. The engine matches buyers using this controlled vocabulary, so a
request that maps cleanly to one of them produces the most consistent scoring
and output. Free-form input still works — the extractor maps it to the closest
sector — but **sticking to the list gives the best results**.

| Sector (`label`) | Covers |
|---|---|
| `energy` | oil & gas, conventional power generation |
| `renewables` | wind, solar, hydro, storage |
| `utilities` | water, electricity & gas networks/distribution |
| `technology` | software, SaaS, IT, semiconductors |
| `telecom` | telecom operators, fiber, mobile |
| `media_entertainment` | media, content, gaming, advertising |
| `financial_services` | banks, insurance, asset/wealth mgmt, fintech |
| `healthcare` | pharma, biotech, medical devices, care providers |
| `consumer_retail` | retail, e-commerce, apparel, consumer goods |
| `food_and_beverage` | food, beverage, agribusiness processing |
| `industrials` | manufacturing, machinery, aerospace & defense |
| `chemicals_materials` | chemicals, metals & mining, building materials |
| `real_estate` | offices, residential, logistics/industrial property |
| `infrastructure` | toll roads, airports, ports, social infra |
| `transport_logistics` | shipping, freight, 3PL operators |
| `business_services` | professional/B2B services, BPO, staffing |
| `automotive` | automakers, components, EV, mobility |
| `other` | anything not captured above |

Good examples:
```text
Find buyers for a 500MW solar PV portfolio in Spain
Who might acquire a UK regional water utility?
Buyers for a distressed German fiber-optic company
```

When you give a request, the engine first prints what it understood, so you can
sanity-check it:
```text
Extracted target_profile: {'target_name': 'German fiber-optic company',
 'target_class': 'company', 'target_sector': 'telecom',
 'target_country': 'Germany', 'size_eur_m': None}
```

---

## 7. Expected output

For each agent that produces text, you'll see a labelled block:

- **`[news_ingestion_agent]`** — a list of recent, real deals found via web search
  (buyers, EV, dates, sources).
- **`[signal_extraction_agent]`** — structured signal objects as plain text,
  one `field: value` block per deal, e.g.:
  ```text
  buyer: Masdar
  seller: Repsol
  target_country: Spain
  target_class: renewable_portfolio
  target_sector: renewables
  event_type: minority_stake
  multiple: 1.2 EUR M/MW
  strategic_driver: market_expansion
  urgency: medium
  ```
- **`[buyer_profiling_agent]`** — JSON buyer profiles (consumed by the scorer).
- **`[deal_matching_agent]`** — a one-line summary, e.g. `4 buyers ranked, 1 excluded.`
  (Full ranked results are passed in state to the strategy agent.)
- **`[strategy_agent]`** — the deliverable: buyers grouped into **Tier 1 / Tier 2 /
  Tier 3 / Excluded** with match scores, rationale, and a final recommendation
  (`recommended_tier_1`, `deal_close_probability`, `overall_strategy_note`).

At the end, a **pipeline summary** prints (agents run, buyers scored, top buyer,
latency), and a full trace is saved to:
```text
observability/logs/full_trace.json
```

---

## 8. Evaluation

The `evals/` folder holds a small evaluation suite. Each eval prints a scorecard
**and** saves a timestamped JSON results file to `evals/results/` (plus a one-line
row in `evals/results/history.md`) — kept as evidence of every run.

| Eval | Checks | Uses model? |
|------|--------|-------------|
| `eval_scoring.py` | Deterministic buyer scoring & ranking — match scores, hard exclusions, unknown-size handling | No |
| `eval_parser.py` | The buyer-profile JSON parser — clean / fenced / prose-wrapped / bare-array / truncated / malformed input | No |
| `eval_memory.py` | Long-term memory round-trip — save, filtered search, ordering, validation | No |
| `eval_extraction.py` | Prompt → structured target (class / sector / country), scored by field match vs an 80% threshold | **Yes** (`gemini-2.5-flash-lite`) |

Three of the four are **fully offline** (no API, no rate limits) and pass
identically every run. The extraction eval is the one that exercises real model
behaviour, so it needs API quota and uses a pass threshold rather than expecting 100%.

Run from the project root:
```bash
python3 evals/eval_scoring.py     # free, instant
python3 evals/eval_parser.py      # free, instant
python3 evals/eval_memory.py      # free, instant
python3 evals/eval_extraction.py  # uses flash-lite (needs quota)
```

Example output:
```text
scoring evaluation  (no model)
==================================================================
  [PASS] perfect fit scores 1.0
  [PASS] weak financial excluded
  ...
==================================================================
24/24 = 100%  ->  PASS  (threshold 100%)
Saved: evals/results/scoring_2026-07-04_08-55-14.json
```

Each results JSON records the timestamp, every check, the score, and PASS/FAIL —
a durable, reviewable record of evaluation runs.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `429 RESOURCE_EXHAUSTED … limit: 5` | Free-tier caps (**5 requests/minute** and a small daily quota, per project) — a full pipeline run needs **~15–25 model calls**, so the free tier cannot complete one | **Enable pay-as-you-go billing** — this is effectively required for end-to-end runs (a run costs only a few cents). Waiting and retrying only postpones the next 429; a new key does **not** help (caps are per project, not per key). |
| `503 UNAVAILABLE … model is overloaded` | Google-side transient overload of the model | Retry shortly; it clears on its own. |
| Only the first word of my request is used | Missing quotes on a one-shot run | Wrap the request in quotes, or use interactive mode (`python3 run.py`). |
| `GOOGLE_API_KEY` errors / auth failures | Key missing or wrong in `orchestrator/.env` | Check the key and that `GOOGLE_GENAI_USE_VERTEXAI=FALSE` is set. |

---

## 10. Project structure

```
ma-signal-intelligence-agent/
│
├── run.py                          ← Entry point (interactive + CLI; Flash-Lite request extraction)
├── requirements.txt                ← Python dependencies
├── .env.template                   ← Copy to orchestrator/.env, then add your key
├── .gitignore
├── LICENSE                         ← Apache License 2.0 (full text)
├── NOTICE                         ← Attribution notice (Apache 2.0)
│
├── orchestrator/
│   ├── __init__.py
│   ├── agent.py                    ← SequentialAgent + App + Runner + SessionService + plugin + compaction
│   ├── compaction.py               ← TruncatingSummarizer (deterministic context compaction, no LLM)
│   └── .env                        ← API key (git-ignored)
│
├── agents/                         ← Individual agent definitions
│   ├── __init__.py
│   ├── _gate.py                    ← Cost gate (skips dead-end model calls)
│   ├── news_ingestion_agent.py     ← Agent 1: google_search
│   ├── signal_extraction_agent.py  ← Agent 2: M&A signal classification (convert_to_eur)
│   ├── buyer_profiling_agent.py    ← Agent 3: buyer profiles + memory (search/save) + retry loop
│   ├── deal_matching_agent.py      ← Agent 4: deterministic scoring (no LLM)
│   └── strategy_agent.py           ← Agent 5: tiered outreach strategy
│
├── tools/                          ← Custom tool definitions
│   ├── __init__.py
│   ├── buyer_scoring_tool.py       ← Weighted match score + hard exclusion rules
│   └── currency_tool.py            ← Live EUR conversion via ECB daily reference rates
│
├── skills/                         ← Shared prompt utilities
│   ├── __init__.py
│   └── anti_hallucination.py       ← Parametric anti-hallucination rules appended to LLM agents
│
├── memory/                         ← Long-term precedent-deal memory
│   ├── __init__.py
│   ├── transaction_store.py        ← save_transaction / search_transactions (JSON persistence)
│   └── transactions.json           ← Accumulated precedent deals (auto-generated)
│
├── observability/                  ← Logging, tracing, metrics
│   ├── __init__.py
│   ├── tracer.py                   ← PipelineTracer: logging · tracing · metrics
│   ├── tracing_plugin.py           ← ADK plugin wiring the tracer to the App
│   └── logs/full_trace.json        ← Full decision trace per run (auto-generated)
│
├── evals/                          ← Evaluation suite
│   ├── eval_scoring.py             ← Deterministic scoring (no model)
│   ├── eval_parser.py              ← JSON parser robustness (no model)
│   ├── eval_memory.py              ← Long-term memory round-trip (no model)
│   ├── eval_extraction.py          ← Prompt → target extraction (Flash-Lite)
│   └── results/                    ← Timestamped JSON evidence + history.md
│
└── docs/
    ├── user_guide.md               ← Full usage guide
    ├── sample_run.md               ← Annotated end-to-end example run
    └── glossary.md                 ← M&A and pipeline terminology
```
