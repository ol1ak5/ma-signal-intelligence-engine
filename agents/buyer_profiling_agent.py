import logging
from typing import AsyncGenerator

from google.adk.agents import Agent, BaseAgent, LoopAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types as genai_types
from tools.currency_tool import convert_to_eur
from memory.transaction_store import save_transaction, search_transactions
from skills.anti_hallucination import anti_hallucination

logger = logging.getLogger(__name__)

buyer_profiling_agent = Agent(
    model='gemini-2.5-flash',
    name='buyer_profiling_agent',
    description='Builds buyer profiles from M&A signals to estimate acquisition appetite and financial capacity.',
    instruction="""
        You are an M&A buyer profiling specialist.

        SCOPE - Profile the 8-10 most relevant buyers in the signals. If more are listed,
        keep only the 10 whose sector, geography, and deal size best fit the target.
        Never research more than 10 as a longer tool-calling run risks an empty final answer.

        RESEARCH - use google_search for each buyer before assessing:
        Revenue and EBITDA over the last 3 years, Net Debt/EBITDA. Always cite source and year.

        MEMORY RECALL - before profiling, call search_transactions (filter by buyer and
        sector) to retrieve precedent deals from the memory bank. Use them to
        inform comparable_deals_count and the typical min/max deal-size range.

        POTENTIAL BUYER PROFILE - build one per buyer identified in the signals:
        - buyer_name
        - buyer_type: corporate / private_equity / infrastructure_fund /
          renewable_platform / family_office / other
        - sector_focus: sectors actively targeted, using the SECTOR taxonomy
          (energy / renewables / utilities / technology / telecom / media_entertainment /
          financial_services / healthcare / consumer_retail / food_and_beverage /
          industrials / chemicals_materials / real_estate / infrastructure /
          transport_logistics / business_services / automotive / other) -
          NOT the asset class, the deal matcher matches on sector.
        - geography_focus: countries or regions actively invested in
        - expansion_mode: acquiring / divesting / deleveraging / neutral /
          unknown (use unknown only if there is truly no evidence either way)
        - revenue_cagr_pct: revenue CAGR over last 3 years in % (e.g. 4.2) -
          omit this field entirely if not found, do not guess
        - ebitda_cagr_pct: EBITDA CAGR over last 3 years in % (e.g. -1.8) -
          omit this field entirely if not found, do not guess
        - financial_capacity - measures whether the buyer is PROFITABLE, not how
          fast it grows. Slow or slightly negative growth does NOT make a large
          profitable company weak:
           * strong   - last-year EBITDA positive AND growing (revenue CAGR >= 5
                        and EBITDA CAGR >= 0)
           * moderate - last-year EBITDA positive but growth flat or declining
           * weak     - last-year EBITDA NEGATIVE (unprofitable), or EBITDA
                        negative across the last 3 years, or clear financial
                        distress (default, restructuring)
           * unknown  - insufficient public data to assess profitability
        - leverage_situation (Net Debt / EBITDA):
            * low     - < 2x
            * medium  - 2x - 4x
            * high    - > 4x
            * unknown - insufficient public data to compute it
        - min_deal_size_eur_m, max_deal_size_eur_m: typical deal range in
          EUR M - omit either field entirely if not found, do not guess
        - investment_plan_eur_m: total capital the buyer has PUBLICLY ANNOUNCED
          it will invest or deploy in this sector (e.g. a "EUR 2Bn FTTH expansion
          plan" -> 2000). Announced plans count even with no closed acquisitions.
          Omit entirely if no announced amount is found, do not guess
        - comparable_deals_count: TOTAL number of comparable deals found in the
          last 5 years, even if more than you list below
        - comparable_deals: up to the 3 MOST RELEVANT of those deals (closest
          in sector, country, and size to the current target) - one dict per deal:
            * target_name
            * target_class
            * target_country
            * transaction_year
            * seller
            * ev_eur_m
            * ev_ebitda (companies or infra only)
            * capacity_mw + ev_mw (renewable portfolios only)
            * cap_rate (real estate only)
          Do not pad this list to hit 3 - fewer, well-sourced deals beat padding.

        CURRENCY CONVERSION:
        All monetary values must be reported in EUR M.
        Use convert_to_eur for any figure found in another currency
        (ev_eur_m, min_deal_size_eur_m, max_deal_size_eur_m, investment_plan_eur_m).

        MEMORY - after profiling each buyer, save every comparable deal to the
        long-term transaction bank using save_transaction. Pass:
        - target_name, target_class, sector, target_country, buyer, seller
        - ev_eur_m (already converted to EUR)
        - ev_ebitda / ev_mw / cap_rate as applicable to the asset class
        - transaction_date in YYYY-MM or YYYY format
        - news_source: the publication name and date you cited for this deal
        Skip save_transaction if ev_eur_m is unknown.

        Once research and saving are done for every buyer in the signals,
        output a single JSON object (no markdown fences, no prose around it):

        {
          "buyer_profiles": [
            {
              "buyer_name": "...",
              "buyer_type": "corporate|private_equity|infrastructure_fund|renewable_platform|family_office|other",
              "sector_focus": ["renewables", ...],
              "geography_focus": ["Spain", ...],
              "expansion_mode": "acquiring|divesting|deleveraging|neutral|unknown",
              "revenue_cagr_pct": 4.2,
              "ebitda_cagr_pct": -1.8,
              "financial_capacity": "strong|moderate|weak|unknown",
              "leverage_situation": "low|medium|high|unknown",
              "min_deal_size_eur_m": 100,
              "max_deal_size_eur_m": 600,
              "investment_plan_eur_m": 2000,
              "comparable_deals_count": 2,
              "comparable_deals": [
                {
                  "target_name": "...", "target_class": "renewable_portfolio",
                  "target_country": "Spain", "transaction_year": "2022",
                  "seller": "...", "ev_eur_m": 450,
                  "capacity_mw": 500, "ev_mw": 900000
                }
              ]
            }
          ]
        }

        Omit optional numeric fields (revenue_cagr_pct, ebitda_cagr_pct,
        min_deal_size_eur_m, max_deal_size_eur_m, investment_plan_eur_m,
        ev_ebitda, capacity_mw, ev_mw, cap_rate) when unknown - never write "N/A".
        Include one entry per buyer in the signals list.
    """ + anti_hallucination(
        missing_data="omit the field entirely - never write \"N/A\" or any "
                      "other placeholder as a value",
        allow_currency_conversion=True,
    ),
    # Guards for this agent's two observed failure modes (details in CLAUDE.md):
    # - thinking_budget cap + max_output_tokens: Gemini 2.5's thinking shares
    #   the output token pool; uncapped, a long tool-heavy turn can spend it
    #   all and return an EMPTY final answer (buyer_data_raw never set).
    # - temperature 0.2: near-greedy decoding reduces MALFORMED_FUNCTION_CALL,
    #   where one bad tool call ends the whole turn with no output.
    generate_content_config=genai_types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=65536,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=8192),
    ),
    output_key='buyer_data_raw',
    tools=[GoogleSearchTool(bypass_multi_tools_limit=True), convert_to_eur,
           save_transaction, search_transactions],
)


class _EnsureProfilesAgent(BaseAgent):
    """Loop checker: stop retrying once buyer_data_raw is populated.

    Backstop for flaky model turns (empty final answer, MALFORMED_FUNCTION_CALL)
    that the config guards above make rarer but cannot eliminate. Escalating
    ends the loop; an empty attempt gets exactly one retry.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        done = bool(str(ctx.session.state.get('buyer_data_raw') or '').strip())
        if not done:
            logger.warning(
                "buyer_data_raw is empty after a profiling attempt "
                "(e.g. MALFORMED_FUNCTION_CALL) - retrying buyer_profiling_agent"
            )
        yield Event(author=self.name, actions=EventActions(escalate=done))


buyer_profiling_loop = LoopAgent(
    name='buyer_profiling_loop',
    sub_agents=[buyer_profiling_agent, _EnsureProfilesAgent(name='profiling_check')],
    max_iterations=2,
)
