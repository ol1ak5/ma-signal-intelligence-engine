"""
Evaluation — buyer-profile JSON parser robustness (no model, no API).

Validates _parse_buyer_profiles() in agents/deal_matching_agent.py: the bridge
that turns the buyer-profiling agent's text output into dicts for the scorer.
Guards against the "0 buyers" failure class.

Run from the project root:
    python3 evals/eval_parser.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from evals._report import save_report
from agents.deal_matching_agent import _parse_buyer_profiles

_CLEAN = json.dumps({"buyer_profiles": [{"buyer_name": "A"}, {"buyer_name": "B"}]})
_BARE_ARRAY = json.dumps([{"buyer_name": "A"}, {"buyer_name": "B"}, {"buyer_name": "C"}])


def _profiles(raw):
    """Unpack the (profiles, warning) tuple - count profiles only."""
    return _parse_buyer_profiles(raw)[0]


def main():
    checks = []

    def check(name, got_len, expected_len):
        checks.append({
            "name": name,
            "passed": got_len == expected_len,
            "detail": f"got {got_len}, expected {expected_len}",
        })

    # realistic variants of how an LLM might return the JSON
    check("clean JSON -> 2", len(_profiles(_CLEAN)), 2)
    check("fenced ```json``` -> 2",
          len(_profiles("```json\n" + _CLEAN + "\n```")), 2)
    check("prose-wrapped JSON -> 2",
          len(_profiles("Here are the profiles:\n" + _CLEAN + "\nHope this helps.")), 2)
    check("dict passthrough -> 1",
          len(_profiles({"buyer_profiles": [{"buyer_name": "A"}]})), 1)
    check("garbage -> [] (safe)", len(_profiles("No JSON here at all.")), 0)
    check("empty string -> [] (safe)", len(_profiles("")), 0)

    # bare [ {...} ] array without the {"buyer_profiles": ...} wrapper -
    # observed in live runs when the model mimics the upstream output style
    check("bare JSON array -> 3", len(_profiles(_BARE_ARRAY)), 3)
    check("fenced bare array -> 3",
          len(_profiles("json\n" + _BARE_ARRAY + "\n")), 3)
    check("list passthrough -> 2",
          len(_profiles([{"buyer_name": "A"}, {"buyer_name": "B"}])), 2)

    # truncated output (a flaky model turn cut mid-JSON) must degrade, not crash
    check("truncated object -> [] (safe)",
          len(_profiles('{"buyer_profiles": [ {"buyer_name": "Cinven"')), 0)
    check("truncated bare array -> [] (safe)",
          len(_profiles('[ {"buyer_name": "A"}, {"buyer_name":')), 0)

    # warning contract: failures warn, successes (incl. bare array) do not
    def warn(raw):
        return _parse_buyer_profiles(raw)[1]
    checks.append({"name": "empty input carries a warning",
                   "passed": warn("") is not None, "detail": ""})
    checks.append({"name": "truncated output carries a warning",
                   "passed": warn('{"buyer_profiles": [ {"x"') is not None, "detail": ""})
    checks.append({"name": "bare array carries no warning",
                   "passed": warn(_BARE_ARRAY) is None, "detail": ""})

    save_report("parser", checks, uses_model=False, threshold=1.0)


if __name__ == "__main__":
    main()
