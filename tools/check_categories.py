"""Assert every metric→category rule names a pair that exists in the taxonomy.

The evaluator scores a category by string equality against the taxonomy's
``sub_category``. A rule naming anything else -- a ``fault_name`` like
``link_loss`` instead of ``loss``, or a pair that was renamed upstream -- does
not raise: ``to_records`` quietly rewrites it to the default, so the incident
is scored as if no metric had fired at all. That is a silent-zero failure, and
it is invisible in the aggregate numbers, so it gets its own check.

Run it after touching METRIC_CATEGORY_RULES or fault_taxonomy.json:

    python tools/check_categories.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiops_challenge_2026.config import load_public_config  # noqa: E402
from baseline.v1.run import (  # noqa: E402
    DEFAULT_CATEGORY,
    METRIC_CATEGORY_RULES,
    NON_DIAGNOSTIC_METRICS,
    _category_for_metric,
)


def main() -> int:
    taxonomy = load_public_config("fault_taxonomy")
    valid = {
        (item["major_category"], item["sub_category"])
        for item in taxonomy["fault_categories"]
    }

    problems: list[str] = []

    if DEFAULT_CATEGORY not in valid:
        problems.append(f"DEFAULT_CATEGORY {DEFAULT_CATEGORY} is not a taxonomy pair")

    for patterns, major, minor in METRIC_CATEGORY_RULES:
        if (major, minor) not in valid:
            problems.append(
                f"rule {patterns} -> {major}/{minor}: not in taxonomy "
                f"(did you mean a sub_category rather than a fault_name?)"
            )

    # Every rule must be reachable: an earlier rule that already matches the
    # same probe metric makes a later one dead code.
    for patterns, major, minor in METRIC_CATEGORY_RULES:
        probe = patterns[0]
        got = _category_for_metric(probe)
        if got != (major, minor):
            problems.append(
                f"rule {patterns} -> {major}/{minor} is shadowed: "
                f"probe {probe!r} resolves to {got}"
            )

    for metric in sorted(NON_DIAGNOSTIC_METRICS):
        if _category_for_metric(metric) is not None:
            problems.append(f"non-diagnostic metric {metric!r} still casts a vote")

    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1

    print(f"OK {len(METRIC_CATEGORY_RULES)} rules, all reachable and in-taxonomy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
