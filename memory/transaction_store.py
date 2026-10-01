import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STORE_PATH = Path(__file__).parent / 'transactions.json'

# Module-level cache — populated on first read, cleared on every write
_cache: list | None = None


def _load() -> list:
    global _cache
    if _cache is None:
        _cache = json.loads(STORE_PATH.read_text()) if STORE_PATH.exists() else []
    return _cache


def _save(records: list) -> None:
    global _cache
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(records, indent=2))
    _cache = records


def save_transaction(
    target_name: str,
    target_class: str,
    sector: str,
    target_country: str,
    buyer: str,
    seller: str,
    ev_eur_m: float,
    news_source: str,
    transaction_date: str = 'unknown',
    ev_ebitda: Optional[float] = None,
    ev_mw: Optional[float] = None,
    cap_rate: Optional[float] = None,
) -> dict:
    """
    Saves a precedent M&A transaction to the long-term memory bank.
    All monetary values are extracted from buyer_profiling_agent output,
    already converted to EUR via convert_to_eur. Do not pass raw figures.
    Multiples are class-specific: ev_ebitda for companies/infra,
    ev_mw for renewable_portfolio, cap_rate for real_estate.

    Args:
        target_name:      name of the acquired asset or company
        target_class:     company | renewable_portfolio | real_estate | infra
        sector:           one of the 18 target sectors (energy, renewables,
                          technology, healthcare, … - see docs/user_guide.md §6)
        target_country:   country of the acquisition target
        buyer:            acquirer name
        seller:           seller name
        ev_eur_m:         enterprise value in EUR millions
        news_source:      publication name and date used as source
        transaction_date: YYYY-MM or YYYY (from source)
        ev_ebitda:        EV/EBITDA multiple (companies and infra only)
        ev_mw:            EV per MW in EUR (renewable portfolios only)
        cap_rate:         capitalisation rate in % (real estate only)

    Returns:
        saved record dict with transaction_id, or error dict on failure
    """
    if not target_name or not buyer or ev_eur_m is None:
        return {'saved': False, 'error': 'target_name, buyer, and ev_eur_m are required'}

    record = {
        'transaction_id':   str(uuid.uuid4()),
        'transaction_date': transaction_date,
        'target_name':      target_name,
        'target_class':     target_class,
        'sector':           sector,
        'target_country':   target_country,
        'buyer':            buyer,
        'seller':           seller,
        'ev_eur_m':         ev_eur_m,
        'ev_ebitda':        ev_ebitda,
        'ev_mw':            ev_mw,
        'cap_rate':         cap_rate,
        'news_source':      news_source,
        'saved_at':         datetime.now(timezone.utc).isoformat(),
    }

    records = _load()
    records.append(record)
    _save(records)

    return {'saved': True, 'transaction_id': record['transaction_id']}


def search_transactions(
    target_class: Optional[str] = None,
    sector: Optional[str] = None,
    target_country: Optional[str] = None,
    buyer: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Searches the precedent transaction memory bank.
    Returns latest transactions first. All filters are optional and additive.

    Args:
        target_class:   filter by asset class
        sector:         filter by sector
        target_country: filter by country
        buyer:          filter by buyer name (partial match)
        limit:          max records to return (default 20)

    Returns:
        dict with 'transactions' list and 'total_found' count
    """
    records = _load()

    for field, value in [('target_class', target_class),
                         ('sector', sector),
                         ('target_country', target_country)]:
        if value:
            records = [r for r in records if r.get(field) == value]

    if buyer:
        records = [r for r in records
                   if buyer.lower() in r.get('buyer', '').lower()]

    # Sort at read time: latest transaction_date first. Undated records
    # ('unknown' or missing) must sink to the end - lexicographically
    # 'unknown' would otherwise sort ABOVE every 'YYYY...' date.
    def _date_key(r: dict) -> str:
        date = r.get('transaction_date') or ''
        return '' if date == 'unknown' else date

    records = sorted(records, key=_date_key, reverse=True)

    return {
        'transactions': records[:limit],
        'total_found':  len(records),
    }
