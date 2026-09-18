"""Is the trend-relative floor calibrated?

_one_sided_floor divides a *lag* scale (how far a trailing median sits behind
the current value) by a *curvature* scale (the second difference). For a ramp
those differ by orders of magnitude, and the docstring's answer is that the
fleet floor catches it. That is only true if the fleet floor is big enough.

For each series this prints the typical and peak score implied by the floor,
so a miscalibration shows up as a typical score far above self_z on a series
with no fault in it.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.v1 import detector as D
from baseline.v1.sources import load_series


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)

    series, _ = load_series(Path(args.data_root).expanduser().resolve())
    params = D.DetectorParams()
    fleet = D._fleet_scales(series)

    rows = []
    for key, points in series.items():
        values = [v for _, v in points]
        trend = D._trend_series(values, params)
        if trend is None:
            continue
        unit = fleet.get(key.metric_name, 0.0)
        floor = D._one_sided_floor(values, params, unit)
        if floor <= 0:
            continue
        residuals = [abs(v - t) for v, t in zip(values, trend)]
        typical = median(residuals)
        rows.append((typical / floor, max(residuals) / floor, floor, unit, key, len(values)))

    rows.sort(key=lambda r: -r[0])
    print(f"{'typ':>9} {'peak':>10} {'floor':>12} {'fleet_u':>12}  series")
    for typ, peak, floor, unit, key, n in rows[: args.top]:
        name = f"{key.region}-{key.role} {key.metric_name}"
        if key.scope:
            name += f" [{key.scope[:28]}]"
        print(f"{typ:9.2f} {peak:10.1f} {floor:12.4g} {unit:12.4g}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
