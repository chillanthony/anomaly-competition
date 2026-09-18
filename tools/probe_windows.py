"""Measure how incident construction behaves as regions are added.

Running the detector on one region and on all eight should give that region the
same answer either way. It does not: xian alone yields 170 incidents, while all
eight regions together yield 86 -- and only 15 of those carry a xian element at
rank one.

Detection is not the problem. Events scale with the number of regions exactly as
they should (6,501 -> 48,992, a factor of 7.5 against 8.1x the points), so the
per-series scoring is behaving. Everything collapses in ``build_incidents``, and
the two volumes that could do the collapsing are both *global* and neither is
scale free:

* ``_candidate_windows`` scores a placement by the element-minutes it covers.
  That number grows with how many regions happen to be loaded -- eight regions
  reporting one flagged element each at the same minute contribute eight, not
  one -- so the maximum over all placements inflates roughly eightfold.
* ``_select_windows`` cuts at ``WINDOW_KEEP_RATIO`` times the *best* candidate.
  The bar is therefore relative to that inflated maximum, and an incident that
  was comfortably the best thing in its own region stops clearing it once seven
  other regions are in the pool.

Both effects predict the same symptom, and this prints the counts, the score
distribution and the cutoff on both sides so which one is binding can be read
off rather than argued about. The per-region incident counts are computed by
running ``build_incidents`` on each region's detections in isolation, which is
the answer that region would get from a single-region run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.v1 import detector as D  # noqa: E402
from baseline.v1 import run as R  # noqa: E402
from baseline.v1.sources import load_series  # noqa: E402


def _span_histogram(windows) -> dict[int, int]:
    return dict(
        sorted(
            Counter(
                int((end - start).total_seconds() // 60) for _, start, end in windows
            ).items()
        )
    )


def _describe(candidates, label: str) -> None:
    if not candidates:
        print(f"{label}: no candidates")
        return
    best = candidates[0][0]
    cutoff = best * R.WINDOW_KEEP_RATIO
    above = [candidate for candidate in candidates if candidate[0] >= cutoff]
    print(
        f"{label}: best={best:.3f} cutoff={cutoff:.3f} "
        f"above_cutoff={len(above)}/{len(candidates)}"
    )
    print(f"{label}: top20 scores {[round(c[0], 2) for c in candidates[:20]]}")
    print(f"{label}: spans of candidates above cutoff {_span_histogram(above)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--regions", nargs="*")
    parser.add_argument("--keep", type=int, default=R.MAX_EVENTS * R.CANDIDATE_RESERVE)
    args = parser.parse_args(argv)

    series, load = load_series(args.data_root, regions=args.regions)
    print(
        json.dumps(
            {
                "regions": args.regions or "all",
                "series": load["series"],
                "points": load["points"],
            },
            indent=2,
        )
    )
    if not series:
        return 2

    allowed = [role for role in R.DEVICE_ROLES if role not in R.EXCLUDED_ROLES]
    detections, stats = D.detect_events(
        series, params=D.DetectorParams(), roles=allowed
    )
    print(json.dumps(stats, indent=2))

    by_region: dict[str, list[D.Detection]] = defaultdict(list)
    for detection in detections:
        by_region[detection.key.region].append(detection)

    print("--- each region's detections scored on their own ---")
    solo_total = 0
    for region in sorted(by_region):
        incidents = R.build_incidents(by_region[region])
        solo_total += len(incidents)
        print(
            f"{region:>10}  detections={len(by_region[region]):>6}  incidents={len(incidents):>5}"
        )
    print(f"{'sum':>10}  incidents={solo_total:>5}")

    print("--- every region pooled, as the full run does it ---")
    grid = R._minute_grid(detections)
    flagged = R._flagged_by_minute(detections)
    candidates = R._candidate_windows(grid, flagged, keep=args.keep)
    print(f"grid_minutes={len(grid)} candidates={len(candidates)}")
    _describe(candidates, "pooled")

    incidents = R.build_incidents(detections)
    print(f"pooled incidents={len(incidents)}")
    print(f"pooled spans={_span_histogram([(i['score'], i['start'], i['end']) for i in incidents])}")

    region_of_record = Counter()
    for incident in incidents:
        counts = Counter(detection.key.region for detection in incident["detections"])
        region_of_record[counts.most_common(1)[0][0]] += 1
    print(f"pooled incidents by dominant region {dict(sorted(region_of_record.items()))}")

    print("--- the same candidates, cut per region instead ---")
    per_region_total = 0
    for region in sorted(by_region):
        subset = by_region[region]
        region_grid = R._minute_grid(subset)
        region_flagged = R._flagged_by_minute(subset)
        region_candidates = R._candidate_windows(region_grid, region_flagged, keep=args.keep)
        kept = R._select_windows(region_candidates, limit=R.MAX_EVENTS)
        per_region_total += len(kept)
        print(
            f"{region:>10}  candidates={len(region_candidates):>6}  "
            f"best={region_candidates[0][0]:.3f}  kept={len(kept):>5}"
        )
    print(f"{'sum':>10}  kept={per_region_total:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
