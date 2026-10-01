"""
Shared result writer for the evals/ harnesses.

Every eval builds a list of checks and calls save_report(). It prints a
scorecard, writes a timestamped JSON results file to evals/results/ (kept as
evidence), and appends one line to evals/results/history.md.
"""
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def save_report(
    eval_name: str,
    checks: list[dict],
    uses_model: bool,
    threshold: float = 1.0,
) -> dict:
    """checks: list of {"name": str, "passed": bool, "detail": str (optional)}."""
    total = len(checks)
    passed = sum(1 for c in checks if c["passed"])
    score = passed / total if total else 0.0
    result = "PASS" if score >= threshold else "FAIL"
    ts = datetime.now()

    record = {
        "eval": eval_name,
        "timestamp": ts.isoformat(timespec="seconds"),
        "uses_model": uses_model,
        "passed": passed,
        "total": total,
        "score": round(score, 4),
        "threshold": threshold,
        "result": result,
        "checks": checks,
    }

    # 1. print scorecard
    tag = "uses model" if uses_model else "no model"
    print(f"\n{eval_name} evaluation  ({tag})")
    print("=" * 66)
    for c in checks:
        line = f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}"
        if c.get("detail") and not c["passed"]:
            line += f"  ({c['detail']})"
        print(line)
    print("=" * 66)
    print(f"{passed}/{total} = {score:.0%}  ->  {result}  (threshold {threshold:.0%})")

    # 2. save timestamped JSON evidence
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{eval_name}_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.json"
    (RESULTS_DIR / fname).write_text(json.dumps(record, indent=2))

    # 3. append to history log
    hist = RESULTS_DIR / "history.md"
    if not hist.exists():
        hist.write_text(
            "# Evaluation history\n\n"
            "| Timestamp | Eval | Score | Result | Model |\n"
            "|---|---|---|---|---|\n"
        )
    with hist.open("a") as f:
        f.write(
            f"| {record['timestamp']} | {eval_name} | "
            f"{passed}/{total} ({score:.0%}) | {result} | "
            f"{'yes' if uses_model else 'no'} |\n"
        )

    print(f"Saved: evals/results/{fname}")
    return record
