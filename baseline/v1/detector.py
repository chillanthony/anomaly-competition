"""Stage-1 anomaly detection: per-series two-sided excursion finding.

Three things separate this from a global five-sigma threshold.

**Merging is scoped to one series.** The shipped detector pools every
anomalous point from every series, sorts them globally, and merges anything
within 120 s. One flapping interface then produces a single 11-day event that
can never overlap a 15-minute ground-truth interval usefully. Here an event is
built from one series and one only, so unrelated symptoms cannot fuse.

**Two independent references.** Each minute is scored against the series' own
history *and* against its cross-region peers at the same minute; the larger
deviation wins. A global five-sigma cannot see a 7 pp step in
``memory_available_ratio`` because that metric's variance across 14 days is
larger than the step -- seven other regions sitting flat at the same minute
can.

**Deviation is compressed before it is ranked.** A raw ``|x - median| / MAD``
is unbounded, and across thousands of near-constant counter series (interface
byte rates, ``*_total`` deltas) the MAD collapses toward zero, so one exporter
restart yields a score of 1e15 and outranks every real incident. Thresholds
are still applied in z units, where they are interpretable, but the *score*
used for ranking and for event magnitude passes through ``log1p``. A 10x
excursion still beats a 3x excursion; a 10^15x artefact cannot.

**A series that never moved is judged against the fleet.** Compression bounds
the *score*, but it cannot stop a degenerate series from clearing the *gate*:
an idle firewall reports ``load1 = 0, 0, 0, 0, 0``, so its baseline spread is
exactly zero, the robust scale bottoms out at 1e-12, and the next routine
tick of 0.07 scores as a ten-billion-sigma event. The same collapse hits the
peer path from the other side -- residuals of a near-constant metric are
themselves near-constant, so their spread is ~1e-6 and a 0.1% wobble reads as
a 100-sigma step. Neither is a property of the fault; both are the yardstick
dividing by nothing. So the scale is floored at a fraction of that metric's
own excursion size measured across the whole fleet, which is never degenerate
and needs no per-metric hand-tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import log1p, sqrt
from statistics import median
from typing import Any, Iterable

from .sources import SeriesKey

# Smallest scale we are willing to divide by, for series whose entire history
# is identically zero.
TINY = 1e-12


@dataclass
class Detection:
    """One anomalous span on one series."""

    key: SeriesKey
    start: datetime
    end: datetime
    score: float
    peak_time: datetime
    peak_magnitude: float
    n_points: int
    points: list[tuple[datetime, float, float]] = field(default_factory=list)

    @property
    def element_id(self) -> str:
        return self.key.element_id


@dataclass
class DetectorParams:
    # Self-reference deviation, in robust-scale units. A point is flagged only
    # when it clears *both* the low bar (min_score) and one of the two
    # per-reference bars (self_z / peer_z).
    self_z: float = 5.0
    # Cross-region peer deviation. Lower, because the peer set is small (<= 7
    # values) and the estimate is correspondingly noisier.
    peer_z: float = 4.0
    # The low bar: a point must beat this under whichever reference it wins on.
    min_score: float = 5.0

    # A robust scale is floored at this fraction of the series' typical
    # magnitude. Counter-rate series are the reason: their minute-to-minute MAD
    # is a fraction of a percent of the level, so an unfloored 5-sigma fires on
    # ordinary jitter thousands of times a day.
    scale_floor_ratio: float = 0.02

    # The baseline a series is judged against comes from its opening segment,
    # not from the whole series. A fault occupying a large share of a short
    # window drags a whole-series median onto the faulty level and inverts the
    # score; on a 14-day phase-1 file the opening segment is thousands of
    # points, far more than any single incident.
    baseline_fraction: float = 0.2
    baseline_points: int = 5

    # Ranking score is log1p(z) per point, and a run's score is the L2 norm of
    # its points. The cap is a safety net, not a working limit: reaching it
    # would need a z of e^40.
    score_cap: float = 40.0

    # Consecutive flagged minutes separated by no more than this stay one
    # event. A couple of minutes of recovery inside one fault is normal.
    merge_gap_minutes: int = 3
    # An excursion longer than this is a baseline shift, not an incident.
    max_event_minutes: int = 40
    # Shorter than this is indistinguishable from sampling noise.
    min_event_minutes: int = 2
    # Ground truth is one fault per incident; requiring corroboration keeps
    # single-point jitter out of the submission.
    min_points: int = 3
    # ...but a run that clears min_points may still be mostly holes. Require
    # this fraction of its own span to be flagged.
    min_density: float = 0.35

    # Peer comparison needs at least this many other regions reporting the same
    # series at the same minute before it is trusted.
    min_peers: int = 4
    # A series that fires in this fraction of its peer regions at the same time
    # is fleet-wide churn (scheduled config pushes, collector restarts), not one
    # region's fault. Ground truth is one fault in one region, so co-firing
    # across the fleet is the single largest source of false positives.
    max_correlated_fraction: float = 0.6
    # Ground truth is minute-grid annotated, so a minute of slack on each side
    # is pure gain against the Dice gate.
    pad_minutes: int = 1


def _quantile(ordered: list[float], fraction: float) -> float:
    """Linear-interpolated quantile of an already-sorted list."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _fleet_scales(
    series: dict[SeriesKey, list[tuple[datetime, float]]],
) -> dict[str, float]:
    """How far one series of each metric moves when nothing is wrong.

    A floor has to be a *magnitude*, not a spread, and it has to survive
    metrics whose fleet is degenerate. Pool every value the fleet reports for a
    metric and take p90 - p50: the excursion size of the bulk. Nine series out
    of ten pinned at zero -- idle counters -- still leave p90 set by the tenth
    series, so the floor stays a real number instead of collapsing to the 1e-12
    guard.

    For the handful of metrics where the *bulk* is degenerate (``process_count``
    is zero on more than 90% of the fleet, ``disk_read_rate`` on 93%), p90 - p50
    is itself zero and we widen to p99 - p50, which the non-degenerate minority
    sets. That also settles the subnormal-float artefact: ``open_fd_ratio``
    reports ~1.5e-16 on most series rather than a true zero, but its p90 is the
    real 6.45e-03 mode, so the floor lands on the real magnitude and the
    1e-16 noise never gets a chance to become the yardstick.
    """
    pooled: dict[str, list[float]] = {}
    for key, points in series.items():
        pooled.setdefault(key.metric, []).extend(value for _, value in points)

    scales: dict[str, float] = {}
    for metric, values in pooled.items():
        values.sort()
        median = _quantile(values, 0.50)
        spread = _quantile(values, 0.90) - median
        if spread <= 0.0:
            spread = _quantile(values, 0.99) - median
        if spread > 0.0:
            scales[metric] = spread
    return scales


def _robust_scale(
    values: list[float],
    centre: float,
    floor_ratio: float,
    fleet_unit: float = 0.0,
) -> float:
    """A robust spread estimate that is never zero for a real series.

    Scaled MAD first; when more than half the values sit exactly on the centre
    (routine for integer and mostly-idle counters) the MAD is zero and we fall
    back to the mean absolute deviation. Either way the result is floored --
    against the series' own magnitude, and against the fleet's excursion size
    for this metric.

    The second floor is what stops a degenerate series from manufacturing a
    detection. ``load1 = 0, 0, 0, 0, 0`` has a spread of exactly zero, so the
    first floor is zero too and the guard value of 1e-12 takes over: the next
    tick of 0.07 divides out to ten billion sigma. Floored against the fleet,
    that same tick is a fraction of a sigma and nothing fires.
    """
    if not values:
        return max(fleet_unit * floor_ratio, TINY)
    floor = max(
        median([abs(value) for value in values]) * floor_ratio,
        fleet_unit * floor_ratio,
        TINY,
    )
    mad = 1.4826 * median([abs(value - centre) for value in values])
    if mad > 0.0:
        return max(mad, floor)
    spread = sum(abs(value - centre) for value in values) / len(values)
    return max(spread, floor)


def _compress(z: float, cap: float) -> float:
    """Bound a deviation while keeping it order-preserving."""
    if z <= 0.0:
        return 0.0
    return min(log1p(z), cap)


def _baseline_segment(
    values: list[float],
    params: DetectorParams,
) -> list[float]:
    """The opening slice a series is judged against.

    Deliberately *not* the whole series: a fault covering much of a short case
    window would otherwise pull the centre onto itself. For phase-1 files this
    is the first ~20% of fourteen days -- thousands of quiet points.
    """
    count = max(params.baseline_points, int(len(values) * params.baseline_fraction))
    return values[:count] if count < len(values) else values


def _self_scores(
    points: list[tuple[datetime, float]],
    params: DetectorParams,
    fleet_unit: float = 0.0,
) -> dict[datetime, float]:
    """Raw deviation of each minute from the series' own opening baseline."""
    values = [value for _, value in points]
    reference = _baseline_segment(values, params)
    centre = median(reference)
    scale = _robust_scale(reference, centre, params.scale_floor_ratio, fleet_unit)
    return {moment: abs(value - centre) / scale for moment, value in points}


def _peer_scores(
    series: dict[SeriesKey, list[tuple[datetime, float]]],
    params: DetectorParams,
    fleet_scales: dict[str, float] | None = None,
) -> dict[SeriesKey, dict[datetime, float]]:
    """Deviation from cross-region peers, self-referenced.

    Peers are the other regions reporting the *same* role, source, metric and
    scope. Comparing across regions rather than across time inside one series
    is what makes a step change visible: a region whose own fourteen-day
    history has drifted further than the fault ever moved it still leaves the
    band its peers occupy.

    Two steps, and both matter. First, each minute becomes a *residual* --
    the region's value minus the peer median at that same minute. Second, the
    residual is scored against its own opening baseline, exactly as a raw
    series is. Without the second step a region that simply runs at a
    different level from its peers (a busier host, a different NIC) sits at a
    large constant residual and fires on every single minute of the file; with
    it, only a *change* in that region's relationship to its peers scores.

    Residuals also cancel fleet-wide events for free: a config push that moves
    every region at once moves the peer median with it, so no region's residual
    stirs. A step in exactly one region -- the shape of a real fault -- cannot
    be cancelled that way.
    """
    groups: dict[tuple[str, str, str, str], dict[str, list[tuple[datetime, float]]]] = {}
    for key, points in series.items():
        groups.setdefault((key.role, key.source, key.metric, key.scope), {})[key.region] = points

    result: dict[SeriesKey, dict[datetime, float]] = {}
    for (role, source, metric, scope), per_region in groups.items():
        if len(per_region) < params.min_peers + 1:
            continue
        by_time: dict[datetime, dict[str, float]] = {}
        for region, points in per_region.items():
            for moment, value in points:
                by_time.setdefault(moment, {})[region] = value

        for region, points in per_region.items():
            residuals: list[tuple[datetime, float]] = []
            for moment, value in points:
                cohort = by_time.get(moment)
                if not cohort or len(cohort) < params.min_peers + 1:
                    continue
                others = [other for name, other in cohort.items() if name != region]
                if len(others) < params.min_peers:
                    continue
                residuals.append((moment, value - median(others)))
            if len(residuals) < params.min_points:
                continue
            values = [value for _, value in residuals]
            reference = _baseline_segment(values, params)
            centre = median(reference)
            # Residuals carry the metric's units, so the fleet floor for that
            # metric applies here too. Without it a near-constant metric gives
            # near-constant residuals whose spread is ~1e-6, and an ordinary
            # 0.1% wobble reads as a hundred-sigma step.
            scale = _robust_scale(
                reference,
                centre,
                params.scale_floor_ratio,
                (fleet_scales or {}).get(metric, 0.0),
            )
            result[SeriesKey(region, role, source, metric, scope)] = {
                moment: abs(value - centre) / scale for moment, value in residuals
            }
    return result


def _split_runs(
    flags: list[tuple[datetime, float, float]],
    params: DetectorParams,
) -> list[list[tuple[datetime, float, float]]]:
    """Group flagged minutes into runs, splitting on gaps and on length.

    Long runs are cut at the allowed maximum rather than discarded, because a
    sustained fault still has to be reported -- just not as one 11-day block.
    """
    runs: list[list[tuple[datetime, float, float]]] = []
    current: list[tuple[datetime, float, float]] = []
    gap = timedelta(minutes=params.merge_gap_minutes)
    span = timedelta(minutes=params.max_event_minutes)
    for item in flags:
        if current and item[0] - current[-1][0] > gap:
            runs.append(current)
            current = []
        elif current and item[0] - current[0][0] > span:
            runs.append(current)
            current = []
        current.append(item)
    if current:
        runs.append(current)
    return runs


def _keep_run(
    run: list[tuple[datetime, float, float]],
    params: DetectorParams,
) -> bool:
    """Decide whether a run is an incident or merely jitter.

    A lone minute is never an incident. The old escape hatch -- keep it if it
    was violent enough -- was the single largest false-positive source in the
    sample suite: a blip on a series that had never moved scored 1e10 and
    cleared any bar, while the real fault's own shape is a *sustained* step.
    Violence is now bounded by the same fleet floor everywhere else, so the
    hatch has nothing left to admit that min_points would not.

    Anything longer must be both corroborated (min_points) and dense: a run of
    three flagged minutes scattered over twenty is three coincidences, not one
    fault.
    """
    span = (run[-1][0] - run[0][0]).total_seconds() / 60.0 + 1.0
    if len(run) < params.min_points:
        return False
    if span < params.min_event_minutes:
        return False
    return len(run) / span >= params.min_density


def _build_detection(
    key: SeriesKey,
    run: list[tuple[datetime, float, float]],
    params: DetectorParams,
) -> Detection | None:
    if not _keep_run(run, params):
        return None
    pad = timedelta(minutes=params.pad_minutes)
    peak = max(run, key=lambda item: item[2])
    # L2 norm over the run's *compressed* deviations rewards intensity *and*
    # persistence: a ten-minute event beats a one-minute spike of the same
    # amplitude, but a single enormous point cannot dwarf ten solid ones the
    # way a plain sum would. The cap is a backstop only -- reaching it would
    # take a z of e^40.
    score = min(
        sqrt(sum(_compress(item[2], params.score_cap) ** 2 for item in run)),
        params.score_cap,
    )
    return Detection(
        key=key,
        start=run[0][0] - pad,
        end=run[-1][0] + pad,
        score=score,
        peak_time=peak[0],
        peak_magnitude=peak[2],
        n_points=len(run),
        points=run,
    )


def detect_events(
    series: dict[SeriesKey, list[tuple[datetime, float]]],
    *,
    params: DetectorParams | None = None,
    roles: Iterable[str] | None = None,
) -> tuple[list[Detection], dict[str, Any]]:
    """Find anomalous spans across every series.

    ``roles`` restricts which device roles may produce a detection; the caller
    uses it to keep the telemetry collector out of the candidate set.
    """
    params = params or DetectorParams()
    allowed = set(roles) if roles else None

    # Every series' scale is floored against the fleet before anything is
    # scored, so a degenerate series cannot out-shout a real one regardless of
    # whether it wins on the self or the peer reference.
    fleet_scales = _fleet_scales(series)
    peer = _peer_scores(series, params, fleet_scales)
    detections: list[Detection] = []
    stats: dict[str, Any] = {
        "series_scored": 0,
        "flagged_points": 0,
        "events_raw": 0,
        "events_dropped": 0,
    }

    for key, points in series.items():
        if allowed is not None and key.role not in allowed:
            continue
        if len(points) < params.min_points:
            continue
        stats["series_scored"] += 1
        points = sorted(points, key=lambda item: item[0])
        self_score = _self_scores(points, params, fleet_scales.get(key.metric, 0.0))
        peer_score = peer.get(key, {})

        # A point is flagged when it clears the low bar under whichever of the
        # two references it wins on, and *that* reference's own bar as well. A
        # minute that is mildly off for both reasons is not an incident.
        flags: list[tuple[datetime, float, float]] = []
        for moment, value in points:
            mine = self_score.get(moment, 0.0)
            theirs = peer_score.get(moment, 0.0)
            best = max(mine, theirs)
            if best < params.min_score:
                continue
            if mine < params.self_z and theirs < params.peer_z:
                continue
            flags.append((moment, value, best))

        if not flags:
            continue
        stats["flagged_points"] += len(flags)

        for run in _split_runs(flags, params):
            event = _build_detection(key, run, params)
            if event is None:
                stats["events_dropped"] += 1
                continue
            detections.append(event)

    stats["events_raw"] = len(detections) + stats["events_dropped"]
    detections.sort(key=lambda item: (-item.score, item.start))
    return detections, stats
