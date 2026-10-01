import requests
import xml.etree.ElementTree as ET

ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
_ECB_NS = {'ecb': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'}

# Module-level cache — populated on first call, reused for the session
_rates_cache: dict | None = None


def _fetch_rates() -> dict:
    """Fetches ECB EUR reference rates and caches them for the session."""
    global _rates_cache
    if _rates_cache is None:
        response = requests.get(ECB_URL, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        rates = {'EUR': 1.0}
        for cube in root.findall('ecb:Cube/ecb:Cube/ecb:Cube[@currency]', _ECB_NS):
            rates[cube.get('currency')] = float(cube.get('rate'))
        if len(rates) == 1:
            # Parsed XML but found no currency cubes (schema change / bad body):
            # fail this call WITHOUT caching, so the next call can retry.
            raise ValueError('ECB response contained no reference rates')
        _rates_cache = rates
    return _rates_cache


def _fail(amount: float, currency: str, reason: str) -> dict:
    return {'amount_eur': None, 'rate': None, 'source': reason,
            'original': f"{amount} {currency}"}


def convert_to_eur(amount: float, currency: str) -> dict:
    """
    Converts a monetary amount to EUR using real-time ECB reference rates.
    Rates are fetched once from ECB and cached for the pipeline run.

    Args:
        amount:   monetary amount to convert
        currency: ISO 4217 code of the source currency (e.g. 'USD', 'GBP', 'CHF')

    Returns dict with:
        amount_eur  - converted amount in EUR (float, or None on failure)
        rate        - exchange rate used: 1 EUR = rate * original currency
        source      - data source for audit trail
        original    - original amount and currency string
    """
    currency = currency.upper().strip()

    if currency == 'EUR':
        return {
            'amount_eur': round(amount, 2),
            'rate':       1.0,
            'source':     'no conversion needed',
            'original':   f"{amount} EUR",
        }

    try:
        rates = _fetch_rates()
    except Exception as e:
        return _fail(amount, currency, f"ECB fetch failed: {e}")

    if currency not in rates:
        return _fail(amount, currency,
                     f"currency {currency} not in ECB reference rates")

    rate = rates[currency]
    return {
        'amount_eur': round(amount / rate, 2),
        'rate':       rate,
        'source':     'ECB daily reference rate',
        'original':   f"{amount} {currency}",
    }
