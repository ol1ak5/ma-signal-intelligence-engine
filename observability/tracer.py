import json
import time
import logging
import os
from datetime import datetime

# ── PILLAR 1: LOGGING SETUP ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

_AGENTS = {
    1: 'News Ingestion',
    2: 'Signal Extraction',
    3: 'Buyer Profiling',
    4: 'Deal Matching',
    5: 'Strategy',
}

# Keys in trace details that map directly to shared metrics
_METRIC_KEYS = frozenset({
    'buyers_profiled', 'buyers_scored', 'top_buyer', 'top_match_score',
})


def _agent_label(n: int) -> str:
    return f"Agent {n} - {_AGENTS[n]}"


class PipelineTracer:
    """
    Observability utility for the M&A Signal Intelligence Engine.

    Pillar 1 - Logging:  what happened and when
    Pillar 2 - Tracing:  full decision trace per pipeline run
    Pillar 3 - Metrics:  performance and quality measurements

    Usage:
        tracer = PipelineTracer(target_query="500MW Solar PV Spain")
        tracer.trace_agent(3, buyers_profiled=8, latency_ms=3200)
        tracer.print_summary()
        tracer.save_trace()
    """

    def __init__(self, target_query: str):
        self.run_id         = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.target_query   = target_query
        self.start_time     = time.time()
        self.pipeline_trace = []

        # ── PILLAR 3: METRICS ────────────────────────────────
        self.metrics = {
            'buyers_profiled':      0,
            'buyers_scored':        0,
            'buyers_excluded':      0,
            'top_match_score':      0.0,
            'top_buyer':            None,
            'tool_call_counts':     {},
            'pipeline_latency_ms':  0,
        }

    # ── PILLAR 1: LOGGING ───────────────────────────────────

    def log_agent_start(self, agent_number: int):
        logger.info(f"[{_agent_label(agent_number)}] started")

    def log_agent_end(self, agent_number: int, latency_ms: int):
        logger.info(f"[{_agent_label(agent_number)}] completed - latency: {latency_ms}ms")

    def log_tool_call(self, agent_number: int, tool_name: str, query: str):
        logger.info(f"[{_agent_label(agent_number)}] called {tool_name}: '{query}'")
        counts = self.metrics['tool_call_counts']
        counts[tool_name] = counts.get(tool_name, 0) + 1

    def log_exclusion(self, buyer_name: str, reason: str):
        logger.info(f"[{_agent_label(4)}] excluded {buyer_name}: {reason}")
        self.metrics['buyers_excluded'] += 1

    # ── PILLAR 2: TRACING ───────────────────────────────────

    def trace_agent(self, agent_number: int, **details):
        """Records full decision trace for one agent step."""
        now = datetime.now()
        entry = {
            'agent':     _agent_label(agent_number),
            'timestamp': now.isoformat(),
            **details,
        }
        self.pipeline_trace.append(entry)

        # Promote known metric keys from details into shared metrics
        self.metrics.update(
            {k: details[k] for k in _METRIC_KEYS if k in details}
        )

        logger.info(f"[{_agent_label(agent_number)}] trace recorded")

    # ── SAVE TRACE ──────────────────────────────────────────

    def save_trace(self, output_path: str = 'observability/logs/full_trace.json'):
        """Saves complete pipeline trace to JSON. Called at end of every run."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        self.metrics['pipeline_latency_ms'] = int(
            (time.time() - self.start_time) * 1000
        )

        trace = {
            'run_id':         self.run_id,
            'target_query':   self.target_query,
            'pipeline_trace': self.pipeline_trace,
            'metrics':        self.metrics,
        }

        with open(output_path, 'w') as f:
            json.dump(trace, f, indent=2)

        logger.info(f"Full trace saved to {output_path}")
        return trace

    # ── PRINT SUMMARY ───────────────────────────────────────

    def print_summary(self):
        """Prints pipeline summary to terminal at end of every run."""
        m = self.metrics
        # 'google_search' - direct builtin tool calls (e.g. news_ingestion_agent).
        # 'google_search_agent' - buyer_profiling_agent's bypass_multi_tools_limit
        # wrapper, which ADK dispatches as an AgentTool under that name.
        search_calls = (
            m['tool_call_counts'].get('google_search', 0)
            + m['tool_call_counts'].get('google_search_agent', 0)
        )

        print(f"""
---------------------------------------------------------
M&A SIGNAL INTELLIGENCE ENGINE - PIPELINE SUMMARY
---------------------------------------------------------
Run ID  : {self.run_id}
Target  : {self.target_query}
---------------------------------------------------------
  {_agent_label(1):<35} signals retrieved
  {_agent_label(2):<35} events classified
  {_agent_label(3):<35} {m['buyers_profiled']} buyer profiles built
  {_agent_label(4):<35} {m['buyers_scored']} buyers scored ({m['buyers_excluded']} excluded)
    Top buyer: {m['top_buyer']} (score: {m['top_match_score']})
  {_agent_label(5):<35} recommendation generated
---------------------------------------------------------
Google Search calls           {search_calls}
Total latency                 {m['pipeline_latency_ms']}ms
---------------------------------------------------------
        """)
        if m.get('buyer_data_parse_warning'):
            print(f"WARNING: {m['buyer_data_parse_warning']}\n")
