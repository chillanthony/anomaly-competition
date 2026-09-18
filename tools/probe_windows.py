"""Measure how incident construction behaves as regions are added.

Running the detector on one region and on all eight should give that region the
same answer either way. It does not: xian alone yields 155 incidents, while the
eighth-region result is 86 pooled, of which only 22 carry a xian element.

Detection is not the problem. Events scale with the number of regions the way
they should, so the per-series scoring is behaving. Everything collapses in
``build_incidents``.

The first version of this probe tested two hypotheses about *the bar*: that
``_candidate_windows`` inflates its maximum by counting element-minutes across
regions, and that ``_select_windows``' relative cutoff therefore rises out of
reach. The measurement refuted both, and the refutation is the finding:

* ``above_cutoff=16000/16000`` -- every retained candidate cleared the bar, so
  the cutoff was not binding at all.
* the pooled top 20 spanned 163.3 down to 158.9, a flat 2.7%, so there was no
  meaningful ranking for a bar to cut anyway.
* ``pooled spans`` put 62 of 86 incidents at 23 minutes.

Flat scores and a ``span ** 0.5`` divisor that grows slower than coverage means
a longer window always wins, so every incident inflates to the cap -- and an
inflated report cannot match a short fault, because a 23-minute prediction
against a true 5-minute fault gives Dice 0.357, under the 0.4 gate. The defect
was in the *score*, not the threshold. ``_candidate_windows`` now scores excess
over the grid's median background density, which is both scale-free across runs
and self-trimming in span.

This probe stays useful as the regression: the span histogram should now peak at
the true span of the incidents rather than at the cap, and the per-region solo
counts should survive pooling. The per-region counts are computed by running
``build_incidents`` on each region's detections in isolation, which is the
answer that region would get from a single-region run.
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
