"""Are the 23-minute windows real, or is WINDOW_MAX_MINUTES padding them?

The full phase-1 run emits 86 records and 62 of them sit at exactly the
24-minute cap (`WINDOW_MAX_MINUTES`), spanning 23 minutes of grid. That is
suspicious: a window that stops at the cap may be a window that wanted to be
longer, or one the scorer could not tell apart from its own middle.

What separates the two is how the evidence is distributed inside the window.
A genuine 23-minute incident puts its element-minutes across the whole span.
An inflated one concentrates them: the same evidence, diluted by minutes that
were only included because the divisor was cheap to pay.

For each selected window this prints where its evidence sits -- the fraction
in the best contiguous half, and the span that would be needed to hold 80% of
it -- so inflation shows up as a window much wider than its own evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.v1 import run as R
from baseline.v1.detector import DetectorParams, detect_events
from baseline.v1.sources import load_series


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--regions", nargs="*", default=None)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)

    params = DetectorParams()
    allowed = [r for r in R.DEVICE_ROLES if r not in R.EXCLUDED_ROLES]
    series, _ = load_series(Path(args.data_root).expanduser().resolve(), regions=args.regions)
    detections, _ = detect_events(series, params=params, roles=allowed)

    grid = R._minute_grid(detections)
    flagged = R._flagged_by_minute(detections)
    limit = R.MAX_EVENTS
    selected = R._select_windows(
        R._candidate_windows(grid, flagged, keep=limit * R.CANDIDATE_RESERVE), limit=limit
    )
    if not selected:
        print("no windows selected")
        return 0

    print(f"grid spans {len(grid)} minutes; {len(selected)} windows selected")
    print(f"cap is {R.WINDOW_MAX_MINUTES} minutes -> {R.WINDOW_MAX_MINUTES - 1} grid minutes\n")
    print(f"{'span':>5} {'besthalf':>9} {'need80%':>8} {'peak1':>6} {'elems':>6}  start")
    widths = []
    for score, start, end in selected[: args.top]:
        first, last = grid.index(start), grid.index(end)
        counts = [len(flagged.get(minute, ())) for minute in grid[first : last + 1]]
        total = sum(counts)
        span = last - first + 1
        if total == 0:
            continue
        # widest half, i.e. the best contiguous run of ceil(span/2) minutes
        half = (span + 1) // 2
        best_half = max(sum(counts[i : i + half]) for i in range(span - half + 1))
        # narrowest prefix-of-sliding-window holding 80% of the evidence
        need = span
        for width in range(1, span + 1):
            if max(sum(counts[i : i + width]) for i in range(span - width + 1)) >= 0.8 * total:
                need = width
                break
        elements = {e for minute in grid[first : last + 1] for e in flagged.get(minute, ())}
        widths.append((span, best_half / total, need / span))
        print(
            f"{span:5d} {best_half / total:9.2f} {need / span:8.2f} {max(counts):6d} {len(elements):6d}  {start:%m-%d %H:%M}"
        )

    if widths:
        n = len(widths)
        print(f"\nmedian span {sorted(w[0] for w in widths)[n // 2]} min;"
              f" median best-half share {sorted(w[1] for w in widths)[n // 2]:.2f};"
              f" median 80%-width share {sorted(w[2] for w in widths)[n // 2]:.2f}")
        print("A best-half share near 0.5 means the evidence fills the span (a real window);")
        print("near 1.0 means half the window is padding (an inflated one).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
