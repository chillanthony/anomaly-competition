"""Measure detector behaviour on one region of the full dataset.

The labelled sample is three incidents in three hours; it cannot tell you that
the detector flags 80% of all minutes on the real 14-day files. This script does
that, and splits every figure by whether the underlying series is monotone,
because that split turned out to be the single most informative number we have.

Usage:
    python tools/probe_region.py --data-root dataset-phase-1/xian_..._data
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.v1 import detector as D
from baseline.v1 import run as R
from baseline.v1.sources import load_series


def _is_monotone(points: list[tuple[datetime, float]], tolerance: float = 0.99) -> bool:
    """Non-decreasing across at least ``tolerance`` of adjacent pairs."""
    values = [value for _, value in points]
    if len(values) < 2:
        return False
    pairs = len(values) - 1
    rises = sum(1 for a, b in zip(values, values[1:]) if b >= a)
    return rises / pairs >= tolerance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--limit", type=int, default=0, help="cap series count, 0 = all")
    args = parser.parse_args(argv)

    root = Path(args.data_root).expanduser().resolve()
    series, stats = load_series(root)
    print(f"loaded {len(series)} series, {stats['points']} points")
    print(f"rows dropped: unknown_identity={stats['rows_dropped_unknown_identity']} "
          f"unknown_region={stats['rows_dropped_unknown_region']}")

    monotone = {key for key, points in series.items() if _is_monotone(points)}
    print(f"monotone series: {len(monotone)} / {len(series)}")

    detects = D.detect_events(series)[0]
    print(f"detections: {len(detects)}")

    # Duty cycle: minute-level, so a single long event does not masquerade as
    # many. Per series, share of its own minutes that were flagged.
    flagged: dict[object, set[datetime]] = defaultdict(set)
    for det in detects:
        for minute, _, _ in det.points:
            flagged[det.key].add(minute)

    def duty(keys) -> float:
        mins = sum(len(flagged.get(key, ())) for key in keys)
        total = sum(len(series[key]) for key in keys)
        return mins / total if total else 0.0

    all_keys = set(series)
    print()
    print(f"duty  all           {duty(all_keys):6.1%}")
    print(f"duty  monotone only {duty(monotone):6.1%}")
    print(f"duty  non-monotone  {duty(all_keys - monotone):6.1%}")

    print()
    print("worst 15 series by duty (>=200 minutes):")
    rows = []
    for key in series:
        if len(series[key]) < 200:
            continue
        share = len(flagged.get(key, ())) / len(series[key])
        rows.append((share, key))
    rows.sort(key=lambda item: -item[0])
    for share, key in rows[:15]:
        tag = "M" if key in monotone else " "
        name = f"{key.region}-{key.role} {key.metric_name}"
        if key.scope:
            name += f" [{key.scope[:40]}]"
        print(f"  {share:6.1%} {tag} n={len(series[key]):5d}  {name}")

    # Which metric names carry the most flagged minutes in total.
    print()
    by_metric: Counter = Counter()
    for key, minutes in flagged.items():
        by_metric[key.metric_name] += len(minutes)
    print("top 15 metric names by flagged minutes:")
    for name, count in by_metric.most_common(15):
        print(f"  {count:7d}  {name}")

    # Window spans. A detector whose windows all sit at the cap has a scoring
    # function that only ever rewards getting longer.
    print()
    grid = R._minute_grid(detects)
    by_minute = R._flagged_by_minute(detects)
    candidate_sets = []
    for keep in (1, 50, 500, 5000):
        candidates = R._candidate_windows(grid, by_minute, keep=keep)
        spans = [round((end - start).total_seconds() / 60) + 1 for _, start, end in candidates]
        distinct = []
        for _, start, end in candidates:
            minute_set = set()
            for minute in grid:
                if start <= minute <= end:
                    minute_set |= by_minute.get(minute, set())
            distinct.append(len(minute_set))
        mean_span = sum(spans) / len(spans) if spans else 0
        print(f"  keep={keep:>5}  n={len(candidates):5d}  span mean={mean_span:5.1f} "
              f"min={min(spans) if spans else 0:3d} max={max(spans) if spans else 0:3d}  "
              f"distinct mean={sum(distinct)/len(distinct) if distinct else 0:5.2f} "
              f"max={max(distinct) if distinct else 0}")

    incidents = R.build_incidents(detects)
    print()
    print(f"incidents: {len(incidents)}")
    span_counts = Counter()
    for incident in incidents:
        minutes = (incident["end"] - incident["start"]).total_seconds() / 60 + 1
        span_counts[round(minutes)] += 1
    print(f"incident spans: {sorted(span_counts.items())[:6]} ... "
          f"{sorted(span_counts.items())[-4:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
