"""
Evaluation — long-term memory round-trip (no model, no API).

Validates memory/transaction_store.py: save_transaction() + search_transactions().
Runs against an isolated temp file so it never touches the real transactions.json.

Run from the project root:
    python3 evals/eval_memory.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from evals._report import save_report
import memory.transaction_store as store


def main():
    # Isolate the store: redirect to a temp file and clear the cache
    store.STORE_PATH = Path(tempfile.mkdtemp()) / "txns.json"
    store._cache = None

    # Seed three precedent deals (dates out of order, to test sorting)
    store.save_transaction("Solar A", "renewable_portfolio", "energy", "Portugal",
                           "Iberdrola", "EDP", 300, "News 2025", transaction_date="2025-06")
    store.save_transaction("Wind B", "renewable_portfolio", "energy", "Spain",
                           "Endesa", "Acciona", 250, "News 2024", transaction_date="2024-01")
    store.save_transaction("Tower C", "real_estate", "real_estate", "France",
                           "Blackstone", "Gecina", 500, "News 2023", transaction_date="2023-09")

    checks = []

    def check(name, cond, detail=""):
        checks.append({"name": name, "passed": bool(cond), "detail": detail})

    all_res = store.search_transactions()
    check("save 3 -> search all returns 3", all_res["total_found"] == 3,
          f"got {all_res['total_found']}")

    energy = store.search_transactions(sector="energy")
    check("filter sector=energy -> 2", energy["total_found"] == 2,
          f"got {energy['total_found']}")

    iber = store.search_transactions(buyer="iberdrola")  # partial, case-insensitive
    check("filter buyer=iberdrola -> 1", iber["total_found"] == 1,
          f"got {iber['total_found']}")

    latest = all_res["transactions"][0]["target_name"] if all_res["transactions"] else None
    check("results sorted latest-first", latest == "Solar A", f"first={latest}")

    # Undated records ('unknown' default) must sink to the END, not sort first
    # ('unknown' > '2026' lexicographically, so a naive reverse sort tops them).
    store.save_transaction("Undated D", "company", "telecom", "Spain",
                           "SomeBuyer", "SomeSeller", 100, "News")  # date defaults to 'unknown'
    ordered = [r["target_name"] for r in store.search_transactions()["transactions"]]
    check("undated record sorts last", ordered[-1] == "Undated D", f"order={ordered}")
    check("dated records still lead", ordered[0] == "Solar A", f"order={ordered}")

    bad = store.save_transaction("X", "company", "telecom", "Germany",
                                 "Buyer", "Seller", None, "News")
    check("missing ev_eur_m rejected", bad.get("saved") is False, f"got {bad}")
    check("rejected save did not persist", store.search_transactions()["total_found"] == 4)

    save_report("memory", checks, uses_model=False, threshold=1.0)


if __name__ == "__main__":
    main()
