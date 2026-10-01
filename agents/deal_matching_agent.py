import json
import logging
import re
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types as genai_types
from tools.buyer_scoring_tool import rank_buyers
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# How much of buyer_profiling_agent's raw output to keep in warnings/traces
# when parsing fails - enough to diagnose, not so much it floods the log.
_RAW_SNIPPET_LEN = 500


def _parse_buyer_profiles(raw) -> tuple[list, str | None]:
    """Parse buyer_profiling_agent output into a list of buyer-profile dicts.

    buyer_profiling_agent writes its result to state via output_key, so `raw`
    is normally the JSON *string* {"buyer_profiles": [ ... ]}. Tolerates a dict
    or list (already parsed), markdown ```json fences, surrounding prose, and
    a bare [ {...}, ... ] array - live runs have shown the model occasionally
    emitting the array without the {"buyer_profiles": ...} wrapper, mimicking
    the upstream agent's output style.

    Returns (profiles, warning). warning is None when parsing succeeded -
    including the legitimate case of buyer_profiling_agent finding zero
    buyers - and is a diagnostic message (with a raw-output snippet)
    whenever its output could not be parsed at all, so a silent "0 buyer
    profiles built" downstream always has a traceable cause upstream.
    """
    if isinstance(raw, dict):
        return raw.get('buyer_profiles', []), None
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)], None

    if not isinstance(raw, str) or not raw.strip():
        warning = "buyer_data_raw was empty - buyer_profiling_agent produced no output"
        logger.warning(warning)
        return [], warning

    stripped = raw.strip()
    parse_detail = ''

    # Fast path: the normal case is a clean JSON string stored via output_key.
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        data = None

    # Fallback: extract the outermost {...} or [...] from fences/prose,
    # trying whichever container opens first.
    if data is None:
        containers = sorted(
            (pos, pattern)
            for pos, pattern in ((stripped.find('{'), r'\{.*\}'),
                                 (stripped.find('['), r'\[.*\]'))
            if pos != -1
        )
        for _, pattern in containers:
            match = re.search(pattern, stripped, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(0))
                break
            except (json.JSONDecodeError, ValueError) as e:
                parse_detail = f' ({e})'

    # Single interpretation point - every path below returns (list, warning).
    if isinstance(data, dict) and 'buyer_profiles' in data:
        return data.get('buyer_profiles') or [], None
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)], None
    if isinstance(data, dict):
        warning = (
            "buyer_data_raw JSON had no 'buyer_profiles' key - raw output: "
            f"{stripped[:_RAW_SNIPPET_LEN]!r}"
        )
        logger.warning(warning)
        return [], warning

    warning = (
        f"buyer_data_raw could not be parsed as JSON{parse_detail} - raw output: "
        f"{stripped[:_RAW_SNIPPET_LEN]!r}"
    )
    logger.warning(warning)
    return [], warning


class DealMatchingAgent(BaseAgent):
    """Custom deterministic agent - no LLM involved.
    Scores and ranks buyers against the target using pure Python math via buyer_scoring_tool.py.

    Scoring weights:
        sector_fit      30%
        geography_fit   20%
        size_fit        20%   (MW for renewable portfolios, EUR otherwise; a
                               buyer with no deal history gets full credit when
                               its announced investment_plan_eur_m covers the
                               target's size)
        financial_score 15%   (if capacity unknown, use a deal-activity proxy
                               from comparable_deals_count, capped at 0.9)
        expansion_score 15%
        + recency bonus +0.05 if a comparable deal closed in the last ~24 months
        Final score is the natural weighted total (0.0 - 1.0).

    Hard exclusion rules:
        - financial_capacity = weak
        - expansion_mode = deleveraging
        - expansion_mode = divesting

    Reads from session state:
        buyer_data_raw  - JSON string from buyer_profiling_agent (output_key),
                          shaped {"buyer_profiles": [ {...}, ... ]}
        target_profile  - target asset profile seeded at startup
                          (target_class, target_sector, target_country,
                           size_eur_m, size_mw - MW used for renewable portfolios)

    Writes to session state:
        deal_matching_results - dict with 'ranked' and 'excluded' buyer lists,
                          plus 'parse_warning' if buyer_data_raw couldn't be
                          parsed (see _parse_buyer_profiles)
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        buyer_data_raw = ctx.session.state.get('buyer_data_raw', '')
        buyer_profiles, parse_warning = _parse_buyer_profiles(buyer_data_raw)
        target_profile = ctx.session.state.get('target_profile', {})

        results = rank_buyers(buyer_profiles, target_profile)
        if parse_warning:
            results['parse_warning'] = parse_warning

        summary = (
            f"{len(results['ranked'])} buyers ranked, "
            f"{len(results['excluded'])} excluded."
        )

        # Persist via state_delta on the event — a direct ctx.session.state
        # assignment is NOT committed by ADK and would be silently lost.
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={'deal_matching_results': results}),
            content=genai_types.Content(
                role='model',
                parts=[genai_types.Part(text=summary)],
            ),
        )


deal_matching_agent = DealMatchingAgent(name='deal_matching_agent')
