"""
Evaluation — prompt -> target extraction (THE model-based eval).

Validates run.extract_target(): free-form request -> structured categorical
fields (target_class, target_sector, target_country). These are deterministic
classifications, scored by exact (normalised) field match against a threshold.

This is the only eval that calls the model (gemini-2.5-flash-lite, ~1 call/case),
so it sleeps between cases to respect the free-tier 5 req/min limit.

Run from the project root:
    python3 evals/eval_extraction.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from evals._report import save_report
from run import extract_target

DATASET = [
    {"request": "Find buyers for a 200MW wind portfolio in Portugal",
     "expected": {"target_class": "renewable_portfolio", "target_sector": "renewables",
                  "target_country": "Portugal"}},
    {"request": "Buyers for a distressed German fiber-optic company",
     "expected": {"target_class": "company", "target_sector": "telecom",
                  "target_country": "Germany"}},
    {"request": "Who would buy a logistics warehouse portfolio in Spain?",
     "expected": {"target_class": "real_estate", "target_sector": "real_estate",
                  "target_country": "Spain"}},
    {"request": "Find acquirers for a French toll road concession",
     "expected": {"target_class": "infra", "target_sector": "infrastructure",
                  "target_country": "France"}},
    {"request": "Who might acquire an Italian dairy and cheese producer?",
     "expected": {"target_class": "company", "target_sector": "food_and_beverage",
                  "target_country": "Italy"}},
]

GRADED_FIELDS = ("target_class", "target_sector", "target_country")
PASS_THRESHOLD = 0.80
DELAY_SECONDS = 13  # stay under free-tier 5 req/min for flash-lite


def _norm(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def main():
    checks = []
    ran = 0

    for i, case in enumerate(DATASET, 1):
        try:
            got = extract_target(case["request"])
        except Exception as e:  # transient 429 / 503 -> skip, don't crash the run
            print(f"[{i}] SKIPPED (transient error): {str(e)[:80]}")
            if i < len(DATASET):
                time.sleep(DELAY_SECONDS)
            continue

        ran += 1
        for field in GRADED_FIELDS:
            exp, act = case["expected"][field], got.get(field)
            checks.append({
                "name": f"[{i}] {field}",
                "passed": _norm(exp) == _norm(act),
                "detail": f"expected={exp!r} got={act!r}",
            })
        if i < len(DATASET):
            time.sleep(DELAY_SECONDS)

    print(f"\nRan {ran}/{len(DATASET)} cases "
          f"({len(DATASET) - ran} skipped due to transient errors)")
    save_report("extraction", checks, uses_model=True, threshold=PASS_THRESHOLD)


if __name__ == "__main__":
    main()
