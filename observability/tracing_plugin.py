"""
ADK Plugin that bridges pipeline lifecycle events into the PipelineTracer.

Registered once on the App, it fires automatically for every agent and tool,
so the agent files stay free of observability code (separation of concerns).

Hooks used:
    before_run   - start a fresh tracer for this run (reads the seeded target)
    before/after_agent - per-agent start/end + latency
    before_tool  - count every tool call (e.g. google_search)
    after_run    - promote final results into metrics, print + save the trace

Note on nested runs: buyer_profiling_agent's GoogleSearchTool uses
bypass_multi_tools_limit=True, which ADK implements by invoking a hidden
'google_search_agent' sub-agent through an AgentTool. That AgentTool spins up
its own Runner and, by default, shares this same plugin instance with it -
so before/after_run_callback would otherwise fire again for every search,
resetting the tracer mid-pipeline. before/after_run_callback are guarded to
only act when the invocation's root agent is our own pipeline, so nested
sub-agent runs are ignored.
"""
import logging
import time

from google.adk.plugins.base_plugin import BasePlugin

from observability.tracer import PipelineTracer, _agent_label

logger = logging.getLogger(__name__)

# Map agent names to the 1-5 numbering PipelineTracer expects
_AGENT_NUMBERS = {
    'news_ingestion_agent':    1,
    'signal_extraction_agent': 2,
    'buyer_profiling_agent':   3,
    'deal_matching_agent':     4,
    'strategy_agent':          5,
}


class TracingPlugin(BasePlugin):
    """Drives a per-run PipelineTracer from ADK lifecycle callbacks."""

    def __init__(self, root_agent_name: str):
        super().__init__(name='ma_tracing')
        self._root_agent_name = root_agent_name
        self.tracer: PipelineTracer | None = None
        self._agent_starts: dict[int, float] = {}
        self._current_agent: int | None = None

    def _is_top_level_run(self, invocation_context) -> bool:
        agent = invocation_context.agent
        return agent is not None and agent.name == self._root_agent_name

    # ── RUN LIFECYCLE ───────────────────────────────────────
    async def before_run_callback(self, *, invocation_context):
        if not self._is_top_level_run(invocation_context):
            return
        target = invocation_context.session.state.get('target_profile', {})
        self.tracer = PipelineTracer(
            target_query=target.get('target_name', 'unknown target')
        )
        self._agent_starts = {}
        self._current_agent = None

    async def after_run_callback(self, *, invocation_context):
        if not self._is_top_level_run(invocation_context):
            return
        if not self.tracer:
            return
        # Promote the deterministic matching results into tracer metrics
        results = invocation_context.session.state.get('deal_matching_results', {})
        ranked = results.get('ranked', [])
        excluded = results.get('excluded', [])

        m = self.tracer.metrics
        m['buyers_profiled'] = len(ranked) + len(excluded)
        m['buyers_scored'] = len(ranked) + len(excluded)
        m['buyers_excluded'] = len(excluded)
        if ranked:
            m['top_buyer'] = ranked[0].get('buyer')
            m['top_match_score'] = ranked[0].get('match_score')
        parse_warning = results.get('parse_warning')
        if parse_warning:
            m['buyer_data_parse_warning'] = parse_warning

        # save_trace() computes pipeline_latency_ms, so it must run before
        # print_summary() reads it - otherwise the printed total is always 0.
        self.tracer.save_trace()
        self.tracer.print_summary()

    # ── AGENT LIFECYCLE ─────────────────────────────────────
    async def before_agent_callback(self, *, agent, callback_context):
        n = _AGENT_NUMBERS.get(agent.name)
        if n is None or not self.tracer:
            return
        self._current_agent = n
        self._agent_starts[n] = time.time()
        self.tracer.log_agent_start(n)

    async def after_agent_callback(self, *, agent, callback_context):
        n = _AGENT_NUMBERS.get(agent.name)
        if n is None or not self.tracer:
            return
        latency_ms = int((time.time() - self._agent_starts.get(n, time.time())) * 1000)
        self.tracer.log_agent_end(n, latency_ms)
        self.tracer.trace_agent(n, latency_ms=latency_ms)

    # ── TOOL LIFECYCLE ──────────────────────────────────────
    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        if self._current_agent is None or not self.tracer:
            return
        query = str(tool_args.get('query', '')) or str(tool_args)
        self.tracer.log_tool_call(self._current_agent, tool.name, query)

    # ── MODEL LIFECYCLE ─────────────────────────────────────
    async def after_model_callback(self, *, callback_context, llm_response):
        """Logs abnormal finish reasons - e.g. MAX_TOKENS - with token usage.

        buyer_profiling_agent has been observed returning a completely empty
        final response after a long tool-calling turn; ADK does not surface
        finish_reason/usage anywhere else, so without this there's no way to
        tell a MAX_TOKENS truncation apart from any other kind of failure.
        """
        if self._current_agent is None:
            return
        finish_reason = getattr(llm_response, 'finish_reason', None)
        if finish_reason in (None, 'STOP'):
            return
        usage = llm_response.usage_metadata
        logger.warning(
            "[%s] model call finished with reason=%s (usage: %s)",
            _agent_label(self._current_agent),
            finish_reason,
            usage.model_dump(exclude_none=True) if usage else None,
        )
