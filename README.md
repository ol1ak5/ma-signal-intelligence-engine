# M&A Signal Intelligence Engine

A multi-agent system turning fragmented M&A signals into actionable buyer intelligence, from data ingestion to strategic recommendations. Watch the Demo on **[YouTube](https://youtu.be/8YsdOT6R3fs)**.

> **Track:** Agents for Business – AI Agents: Intensive Vibe Coding Capstone Project

## Why It Matters

Deals rarely fail for lack of buyers. They fail because teams call the wrong buyers at the wrong time, working from static relationship maps and missing live signals like leverage events, divestments and strategic pivots.

The M&A Signal Intelligence Engine, built with Google ADK and Gemini 2.5 Flash, reads those signals and finds the right buyers for a given company or asset, explaining the reasoning behind each choice.

## How It Works

```mermaid
flowchart LR
    INPUT["Request"]
    A["News<br>Ingestion"]
    B["Signal<br>Extraction"]
    C["Buyer<br>Profiling"]
    D["Deal<br>Matching"]
    E["Strategy"]
    OUTPUT["Ranked buyers<br>+ outreach plan"]

    MEM[("Memory<br>Bank")]
    OBS["Observability"]

    INPUT --> A --> B --> C --> D --> E --> OUTPUT
    C <--> MEM
    A & B & C & D & E -.-> OBS
```

## Quick Start

### Prerequisites

- **Python 3.10+** (developed on 3.11)
- A **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey)

### 1. Clone & enter

```bash
git clone https://github.com/ol1ak5/ma-signal-intelligence-engine.git
cd ma-signal-intelligence-engine
```

### 2. Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your API key

```bash
cp .env.template orchestrator/.env
# then edit orchestrator/.env and paste your key:
#   GOOGLE_API_KEY="your_gemini_api_key_here"
```

> **Security:** `orchestrator/.env` is git-ignored, so never commit your key.

### 5. Run the pipeline

```bash
# Interactive (no quotes needed):
python3 run.py
#   → Describe the acquisition target (or press Enter for the built-in example):
#   > 500MW solar PV portfolio in Spain

# One-shot (quotes required):
python3 run.py "Find buyers for a distressed German fiber-optic company"
```

## Project Structure

```
ma-signal-intelligence-engine/
├── run.py           ← Entry point (interactive + CLI)
├── orchestrator/    ← SequentialAgent, App, sessions, context compaction
├── agents/          ← The five pipeline agents
├── tools/           ← Buyer scoring and EUR conversion
├── skills/          ← Anti-hallucination prompt rules
├── memory/          ← Transaction Memory Bank (JSON)
├── observability/   ← Tracing plugin, logs, metrics
├── evals/           ← Eval suite and saved results
└── docs/            ← User guide, sample run, glossary
```

## Course Concepts Demonstrated (6 of 8)

**1. Multi-Agent Systems** ✅
- 4 LLM agents (`news_ingestion`, `signal_extraction`, `buyer_profiling`, `strategy`)
- 1 custom agent (`DealMatchingAgent` – deterministic `BaseAgent`, no LLM)
- 2 workflow agents (`ma_signal_intelligence_engine` – `SequentialAgent` orchestrating the pipeline; `buyer_profiling_loop` – `LoopAgent` that retries buyer profiling once if a flaky model turn returns empty output)

**2. Tools** ✅
- **Built-in:** `google_search` (real-time M&A news and buyer financial data)
- **Custom (`FunctionTool`):** `convert_to_eur` (live EUR conversion via ECB reference rates), `score_buyer` / `rank_buyers` (deterministic scoring), `save_transaction` / `search_transactions` (memory bank)

**3. Sessions & Memory** ✅
- `InMemorySessionService` + inter-agent state (`output_key`, `EventActions state_delta`, prompt injection)
- **Long-term memory:** `transaction_store.py` persists precedent deals across runs

**4. Context engineering** ✅
- **Context compaction:** deterministic context compaction (`TruncatingSummarizer` via `EventsCompactionConfig`)

**5. Observability** ✅
- Logging · Tracing · Metrics via `PipelineTracer`, wired as an `App` plugin; full trace JSON per run

**6. Agent Evaluation** ✅
- 4-script eval suite (3 offline + 1 model-based) with timestamped, saved evidence

> **Minimum requirement: 3 concepts vs. implemented: 6 ✅**

## License

Copyright © 2026 Olga Aksenova.

The code in this repository is licensed under the **[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0)** – see
[LICENSE](LICENSE) for the full text.

*Built for AI Agents: Intensive Vibe Coding Capstone Project*
