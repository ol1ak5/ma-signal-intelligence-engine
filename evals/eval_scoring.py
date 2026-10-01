"""
Evaluation — deterministic buyer scoring (no model, no API).

Validates tools/buyer_scoring_tool.py: score_buyer() and rank_buyers().
Pure Python -> fast, free, reproducible, unaffected by rate limits.

Run from the project root:
    python3 evals/eval_scoring.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from evals._report import save_report
from tools.buyer_scoring_tool import score_buyer, rank_buyers

TARGET = {
    "target_name": "200MW Wind Portfolio", "target_class": "renewable_portfolio",
    "target_sector": "energy", "target_country": "Portugal", "size_eur_m": 300,
}


def buyer(name, **over):
    base = {
        "buyer_name": name, "sector_focus": ["energy"], "geography_focus": ["Portugal"],
        "expansion_mode": "acquiring", "financial_capacity": "strong",
        "leverage_situation": "low", "min_deal_size_eur_m": 100,
        "max_deal_size_eur_m": 600, "comparable_deals_count": 3,
    }
    base.update(over)
    return base


def main():
    checks = []

    def check(name, cond, detail=""):
        checks.append({"name": name, "passed": bool(cond), "detail": detail})

    # Perfect fit -> full weighted total (1.0), not excluded
    r = score_buyer(buyer("Perfect"), TARGET)
    check("perfect fit scores 1.0", r["match_score"] == 1.0, f"got {r['match_score']}")
    check("perfect fit not excluded", r["excluded"] is False)

    # Hard exclusions
    check("weak financial excluded",
          score_buyer(buyer("Weak", financial_capacity="weak"), TARGET)["excluded"])
    check("deleveraging excluded",
          score_buyer(buyer("Delev", expansion_mode="deleveraging"), TARGET)["excluded"])
    check("divesting excluded",
          score_buyer(buyer("Div", expansion_mode="divesting"), TARGET)["excluded"])

    # Partial fit: scored, not excluded, below perfect
    partial = score_buyer(buyer("Partial", sector_focus=["technology"],
                                geography_focus=["Spain"]), TARGET)
    check("partial fit not excluded", partial["excluded"] is False)
    check("partial fit < perfect", partial["match_score"] < 1.0,
          f"got {partial['match_score']}")

    # Unknown size -> no crash, neutral size_fit
    unk = score_buyer(buyer("NoSize"), {**TARGET, "size_eur_m": None})
    check("unknown size handled (no crash)", unk["match_score"] is not None)
    check("unknown size -> size_fit medium", unk["size_fit"] == "medium",
          f"got {unk['size_fit']}")

    # Degenerate focus entries (LLM artifacts like '') earn no substring credit
    junk = score_buyer(buyer("JunkFocus", sector_focus=[""], geography_focus=[""]), TARGET)
    check("empty-string sector focus -> sector_fit low",
          junk["sector_fit"] == "low", f"got {junk['sector_fit']}")
    check("empty-string geography focus -> geography_fit low",
          junk["geography_fit"] == "low", f"got {junk['geography_fit']}")

    # Unknown TARGET country / sector (vague request) -> neutral, not punitive
    nocty = score_buyer(buyer("NoCountryTarget"), {**TARGET, "target_country": None})
    check("unknown target country -> geography_fit medium",
          nocty["geography_fit"] == "medium", f"got {nocty['geography_fit']}")
    nosec = score_buyer(buyer("NoSectorTarget"), {**TARGET, "target_sector": None})
    check("unknown target sector -> sector_fit medium",
          nosec["sector_fit"] == "medium", f"got {nosec['sector_fit']}")

    # Buyer with no stated deal-size range -> neutral size_fit, not a free 1.0
    norange = score_buyer(
        buyer("NoRange", min_deal_size_eur_m=None, max_deal_size_eur_m=None), TARGET)
    check("no buyer deal-size range -> size_fit medium",
          norange["size_fit"] == "medium", f"got {norange['size_fit']}")

    # Announced investment plan: with no deal-size history, a public plan that
    # covers the target's size earns full size credit (DIGI's EUR 2B FTTH case).
    plan_big = score_buyer(buyer("BigPlan", min_deal_size_eur_m=None, max_deal_size_eur_m=None,
                                 investment_plan_eur_m=2000), TARGET)
    check("investment plan covering target -> size_fit high",
          plan_big["size_fit"] == "high", f"got {plan_big['size_fit']}")
    plan_small = score_buyer(buyer("SmallPlan", min_deal_size_eur_m=None, max_deal_size_eur_m=None,
                                   investment_plan_eur_m=100), TARGET)
    check("investment plan below target -> size_fit stays medium",
          plan_small["size_fit"] == "medium", f"got {plan_small['size_fit']}")

    # Class-aware size: a renewable_portfolio target with MW data is sized on MW
    # (from the buyer's comparable-deal capacities), not EUR.
    mw_target = {**TARGET, "size_mw": 500, "size_eur_m": 450}
    # MW range brackets 500 -> high, even though the EUR range (1000-1200) would say low
    big_mw = score_buyer(buyer("BigMW", comparable_deals=[{"capacity_mw": 300}, {"capacity_mw": 700}],
                               min_deal_size_eur_m=1000, max_deal_size_eur_m=1200), mw_target)
    check("MW target within buyer MW range -> size_fit high",
          big_mw["size_fit"] == "high", f"got {big_mw['size_fit']}")
    # Buyer of tiny MW portfolios -> 500MW far too big (MW overrides a favourable EUR range)
    tiny_mw = score_buyer(buyer("TinyMW", comparable_deals=[{"capacity_mw": 50}],
                                min_deal_size_eur_m=100, max_deal_size_eur_m=600), mw_target)
    check("MW target far above buyer MW range -> size_fit low",
          tiny_mw["size_fit"] == "low", f"got {tiny_mw['size_fit']}")
    # No MW data on the buyer -> EUR fallback (450 within 100-600 -> high)
    eur_fb = score_buyer(buyer("EURfallback", comparable_deals=[],
                               min_deal_size_eur_m=100, max_deal_size_eur_m=600), mw_target)
    check("no buyer MW data -> EUR fallback -> size_fit high",
          eur_fb["size_fit"] == "high", f"got {eur_fb['size_fit']}")

    # Recency bonus: a recent comparable deal scores higher than a stale one.
    # Use a partial-sector buyer so both stay below the 1.0 cap (headroom for the bonus).
    recent = score_buyer(buyer("Recent", sector_focus=["technology"],
                                comparable_deals=[{"transaction_year": str(date.today().year)}]), TARGET)
    stale = score_buyer(buyer("Stale", sector_focus=["technology"],
                              comparable_deals=[{"transaction_year": "2015"}]), TARGET)
    check("recent comparable deal scores higher than a stale one",
          recent["match_score"] > stale["match_score"],
          f"recent={recent['match_score']} stale={stale['match_score']}")
    check("recent buyer gets a recency bonus", recent["recency_bonus"] > 0,
          f"got {recent['recency_bonus']}")

    # Capacity proxy: when financials are unknown, comparable-deal history lifts
    # the score. Isolate from recency by making BOTH buyers' deals old (no bonus).
    prolific = score_buyer(buyer("Prolific", sector_focus=["technology"], financial_capacity="unknown",
                                 comparable_deals_count=6,
                                 comparable_deals=[{"transaction_year": "2016"}]), TARGET)
    nohist = score_buyer(buyer("NoHistory", sector_focus=["technology"], financial_capacity="unknown",
                               comparable_deals_count=0, comparable_deals=[]), TARGET)
    check("unknown-financial capacity proxy: deal history beats none",
          prolific["match_score"] > nohist["match_score"],
          f"prolific={prolific['match_score']} none={nohist['match_score']}")

    # rank_buyers orders and separates excluded
    res = rank_buyers([buyer("Weak", financial_capacity="weak"),
                       buyer("Partial", sector_focus=["technology"], geography_focus=["Spain"]),
                       buyer("Perfect")], TARGET)
    ranked = [b["buyer"] for b in res["ranked"]]
    check("excluded separated from ranked", "Weak" not in ranked)
    check("ranked sorted desc (Perfect first)", ranked[:1] == ["Perfect"],
          f"order={ranked}")

    save_report("scoring", checks, uses_model=False, threshold=1.0)


if __name__ == "__main__":
    main()
