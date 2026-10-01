import re
from datetime import date

# Recency bonus: buyers that actually closed a comparable deal in the last ~24
# months are hotter leads. A small flat bonus is added on top of the weighted
# score (capped at 1.0). Tunable here.
RECENCY_WINDOW_YEARS = 2   # "recent" = current year or the previous year (~24 months)
RECENCY_BONUS = 0.05


def _safe_float(value, default: float) -> float:
    """Converts a profile field to float, returning default for missing/invalid values."""
    try:
        return float(value) if value not in (None, 'N/A', '') else default
    except (TypeError, ValueError):
        return default


def _parse_year(value) -> int | None:
    """Extract a 4-digit year (int) from a transaction_year/date value, or None."""
    if value in (None, '', 'unknown', 'N/A'):
        return None
    match = re.search(r'(?:19|20)\d{2}', str(value))
    return int(match.group(0)) if match else None


def _capacity_from_deal_count(n: int) -> float:
    """Behavioral proxy for financial capacity from comparable-deal count, used
    only when reported financial_capacity is 'unknown'. Capped at 0.9 so it never
    fully impersonates verified 'strong' financials."""
    if n >= 6:
        return 0.9
    if n >= 3:
        return 0.7
    if n >= 1:
        return 0.5
    return 0.2


def _size_fit(target_size: float, min_size: float, max_size: float, has_range: bool) -> float:
    """Banded size fit, shared by the EUR and MW paths.
    1.0 within the buyer's typical range · 0.5 slightly outside or unknown ·
    0.1 far outside (< half the min or > twice the max)."""
    if not target_size:
        return 0.5   # unknown target size - neutral, neither rewarded nor penalised
    if not has_range:
        return 0.5   # buyer's typical size unknown - neutral, not a free 1.0
    if min_size <= target_size <= max_size:
        return 1.0
    if target_size < min_size * 0.5 or target_size > max_size * 2:
        return 0.1
    return 0.5       # slightly outside but feasible


def _to_label(score: float) -> str:
    """Converts numeric fit score to high/medium/low label."""
    if score >= 0.9:
        return 'high'
    elif score >= 0.5:
        return 'medium'
    else:
        return 'low'


_EXCLUSION_REASONS = {
    'weak':         'weak financial capacity',
    'deleveraging': 'currently deleveraging - not deploying capital',
    'divesting':    'actively divesting - reducing portfolio size',
}


def score_buyer(buyer_profile: dict, target_profile: dict) -> dict:
    """
    Deterministically scores buyer-target fit.
    No LLM - pure Python math for full auditability.
    Variables match exactly deal_matching_agent definition.
    Same input always produces same output.
    """

    # Hoist repeated lookups to locals
    financial_capacity = buyer_profile.get('financial_capacity', 'unknown')
    expansion_mode     = buyer_profile.get('expansion_mode', 'neutral')
    leverage_situation = buyer_profile.get('leverage_situation', 'unknown')
    comparable_deals   = buyer_profile.get('comparable_deals_count', 0)
    deal_history       = buyer_profile.get('comparable_deals', [])

    # ── SECTOR FIT ──────────────────────────────────────────
    # Match the buyer's sector focus against the target SECTOR (energy / renewables / technology / …),
    # not the asset class (company / renewable_portfolio / …) — different taxonomies.
    sector_focus  = buyer_profile.get('sector_focus', [])
    target_sector = target_profile.get('target_sector', '')

    if not target_sector:
        sector_fit = 0.5   # target sector unknown - neutral, don't punish every buyer
    elif target_sector in sector_focus:
        sector_fit = 1.0   # high - core investment focus
    elif any(s and s in target_sector for s in sector_focus):
        sector_fit = 0.5   # medium - adjacent sector
    else:
        sector_fit = 0.1   # low - outside typical focus

    # ── GEOGRAPHY FIT ───────────────────────────────────────
    geography_focus = buyer_profile.get('geography_focus', [])
    target_country  = target_profile.get('target_country', '')

    if not target_country:
        geography_fit = 0.5   # target country unknown - neutral, don't punish every buyer
    elif target_country in geography_focus:
        geography_fit = 1.0   # high - core geography
    elif any(g and g in target_country for g in geography_focus):
        geography_fit = 0.6   # medium - invested in region before
    else:
        geography_fit = 0.1   # low - outside typical geography

    # ── SIZE FIT (class-aware) ──────────────────────────────
    # Renewable portfolios are sized by MW capacity, not price: a buyer of big
    # portfolios finds a small-MW target too small regardless of EUR value (and
    # vice versa). For renewable_portfolio targets with a known MW size we compare
    # MW - deriving the buyer's typical MW range from its comparable-deal
    # capacities. Every other asset class (and any case without MW data) uses the
    # EUR range. A missing buyer range on either path is treated as neutral, not a
    # free 1.0, so proven-range buyers rank above intent-only ones.
    target_class = target_profile.get('target_class', '')
    target_eur   = _safe_float(target_profile.get('size_eur_m'), 0)
    target_mw    = _safe_float(target_profile.get('size_mw'), 0)
    deal_mws     = [c for c in (_safe_float(d.get('capacity_mw'), 0) for d in deal_history) if c > 0]

    raw_min = buyer_profile.get('min_deal_size_eur_m')
    raw_max = buyer_profile.get('max_deal_size_eur_m')
    has_eur_range = not (raw_min in (None, 'N/A', '') and raw_max in (None, 'N/A', ''))

    if target_class == 'renewable_portfolio' and target_mw and deal_mws:
        size_fit = _size_fit(target_mw, min(deal_mws), max(deal_mws), has_range=True)
    else:
        size_fit = _size_fit(
            target_eur,
            _safe_float(raw_min, 0), _safe_float(raw_max, float('inf')), has_eur_range,
        )

    # A buyer with no acquisition track record may still have PUBLICLY ANNOUNCED
    # investment plans (e.g. DIGI's EUR 2B FTTH expansion). If the committed
    # amount covers the target's size, the target clearly fits their spending
    # scale - full size credit instead of the neutral 0.5 for "no history".
    if not deal_mws and not has_eur_range:
        plan = _safe_float(buyer_profile.get('investment_plan_eur_m'), 0)
        if plan and target_eur and plan >= target_eur:
            size_fit = 1.0

    # ── RECENCY ─────────────────────────────────────────────
    # Most recent comparable-deal year and whether it is within the recency
    # window (~24 months). Feeds both the capacity proxy and the score bonus.
    deal_count = int(_safe_float(comparable_deals, 0))
    deal_years = [y for y in (_parse_year(d.get('transaction_year')) for d in deal_history)
                  if y is not None]
    most_recent_deal_year = max(deal_years) if deal_years else None
    is_recent_buyer = (
        most_recent_deal_year is not None
        and most_recent_deal_year >= date.today().year - (RECENCY_WINDOW_YEARS - 1)
    )

    # ── FINANCIAL CAPACITY ──────────────────────────────────
    financial_score = {
        'strong':   1.0,
        'moderate': 0.6,
        'weak':     0.2,
        'unknown':  0.2,
    }.get(financial_capacity, 0.4)

    # When financials are unknown (common for SWFs / private funds), fall back to
    # a behavioral proxy: comparable-deal activity signals real capacity and
    # appetite. Capped at 0.9 (never a verified 'strong'); the full proxy is only
    # credited when activity is RECENT, so prolific-but-stale buyers don't over-score.
    if financial_capacity == 'unknown':
        proxy = _capacity_from_deal_count(deal_count)
        financial_score = proxy if is_recent_buyer else min(proxy, 0.5)

    # ── EXPANSION MODE ──────────────────────────────────────
    expansion_score = {
        'acquiring':    1.0,
        'neutral':      0.5,
        'divesting':    0.0,
        'deleveraging': 0.0,
    }.get(expansion_mode, 0.5)

    # ── RECENCY BONUS ───────────────────────────────────────
    # A recent comparable deal also earns a small flat bonus on the final score.
    recency_bonus = RECENCY_BONUS if is_recent_buyer else 0.0

    # ── FINAL MATCH SCORE ───────────────────────────────────
    # The natural weighted total (weights reflect M&A deal importance) plus the
    # recency bonus. min(1.0, ...) only trims the rare case where the bonus would
    # push a top buyer above 1.0 - it is not an artificial ceiling.
    match_score = round(
        min(1.0,
            sector_fit      * 0.30 +   # 30% - sector alignment
            geography_fit   * 0.20 +   # 20% - geography alignment
            size_fit        * 0.20 +   # 20% - deal size fit
            financial_score * 0.15 +   # 15% - financial capacity (CAGR-based)
            expansion_score * 0.15 +   # 15% - expansion mode
            recency_bonus              # +0.05 flat if a comparable deal is recent
        ),
        2
    )

    # ── HARD EXCLUSION ──────────────────────────────────────
    exclusion_reason = (
        _EXCLUSION_REASONS.get(financial_capacity)
        or _EXCLUSION_REASONS.get(expansion_mode)
    )
    excluded = exclusion_reason is not None

    # Cache labels to avoid recomputing for return dict and reasoning
    sector_label    = _to_label(sector_fit)
    geography_label = _to_label(geography_fit)
    size_label      = _to_label(size_fit)

    return {
        'buyer':                  buyer_profile.get('buyer_name'),
        'match_score':            match_score,
        'sector_fit':             sector_label,
        'geography_fit':          geography_label,
        'size_fit':               size_label,
        'financial_capacity':     financial_capacity,
        'leverage_situation':     leverage_situation,
        'expansion_mode':         expansion_mode,
        'comparable_deals_count': comparable_deals,
        'most_recent_deal_year':  most_recent_deal_year,
        'recency_bonus':          recency_bonus,
        'excluded':               excluded,
        'exclusion_reason':       exclusion_reason,
        'reasoning': (
            f"Sector: {sector_label} | "
            f"Geography: {geography_label} | "
            f"Size: {size_label} | "
            f"Financial capacity: {financial_capacity} | "
            f"Leverage: {leverage_situation} | "
            f"Expansion mode: {expansion_mode} | "
            f"Most recent deal: {most_recent_deal_year or 'n/a'}"
            f"{' (+recency bonus)' if recency_bonus else ''}"
        ),
    }


def rank_buyers(buyers: list, target_profile: dict) -> dict:
    """
    Scores and ranks all buyers against the target.
    Excludes buyers with weak financial capacity, divesting or deleveraging mode.
    Returns:
        'ranked'   - eligible buyers sorted by match_score descending
        'excluded' - excluded buyers with exclusion_reason
    """
    ranked = []
    excluded = []

    for buyer in buyers:
        result = score_buyer(buyer, target_profile)
        if result['excluded']:
            excluded.append(result)
        else:
            ranked.append(result)

    return {
        'ranked':   sorted(ranked, key=lambda x: x['match_score'], reverse=True),
        'excluded': excluded,
    }
