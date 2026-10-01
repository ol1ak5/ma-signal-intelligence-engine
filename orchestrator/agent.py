from google.adk.agents import SequentialAgent
from google.adk.apps import App
from google.adk.apps._configs import EventsCompactionConfig
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from observability.tracing_plugin import TracingPlugin
from orchestrator.compaction import TruncatingSummarizer
from agents.news_ingestion_agent import news_ingestion_agent
from agents.signal_extraction_agent import signal_extraction_agent
from agents.buyer_profiling_agent import buyer_profiling_loop
from agents.deal_matching_agent import deal_matching_agent
from agents.strategy_agent import strategy_agent

APP_NAME = 'ma_signal_intelligence_engine'

# ── PIPELINE ────────────────────────────────────────────────
root_agent = SequentialAgent(
    name=APP_NAME,
    description='Reads real-time market news and helps M&A teams identify '
                'and rank the most likely buyers for the acquisition target.',
    sub_agents=[
        news_ingestion_agent,
        signal_extraction_agent,
        buyer_profiling_loop,   # LoopAgent: retries buyer_profiling_agent once on empty output
        deal_matching_agent,
        strategy_agent,
    ],
)

# ── APP ─────────────────────────────────────────────────────
# Bundles the pipeline with plugins (observability tracer fires for every
# agent and tool, without touching the agents themselves) and context
# compaction (a deterministic, no-API summarizer that condenses older events).
app = App(
    name=APP_NAME,
    root_agent=root_agent,
    plugins=[TracingPlugin(root_agent_name=APP_NAME)],
    events_compaction_config=EventsCompactionConfig(
        summarizer=TruncatingSummarizer(),  # deterministic, no model call
        compaction_interval=40,             # buyer_profiling_agent needs ~30+ events; compact only at end
        overlap_size=2,                     # keep 2 events of overlap
    ),
)

# ── SESSION SERVICE ─────────────────────────────────────────
# Manages state shared between all agents in one pipeline run
session_service = InMemorySessionService()

# ── RUNNER ──────────────────────────────────────────────────
# Connects the app to the session service
runner = Runner(
    app=app,
    session_service=session_service,
)
