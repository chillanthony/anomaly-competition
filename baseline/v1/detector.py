"""Stage-1 anomaly detection: per-series two-sided excursion finding.

Why this is not a plain global threshold
----------------------------------------
The shipped detector pools every anomalous point from every series, sorts them
globally and merges anything within 120 s into one event. One flapping
interface then produces a single 11-day event that can never overlap a
15-minute ground-truth interval usefully. Two changes fix that:

* Merging is scoped to a single series, so unrelated series can never combine
  into one event.
* A point is scored against two independent references -- its own history and
  its cross-region peers at the same minute -- and the larger deviation wins.
  A global five-sigma cannot see a 7 pp step in ``memory_available_ratio``
  because the metric's own variance across 14 days is larger than the step;
  seven other regions sitting flat at that minute can.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

from .sources import SeriesKey


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
    # Self-reference deviation, in scaled MAD units. 6 is high enough that a
    # busy 40 000-minute series does not cross it by chance.
    self_z: float = 6.0
    # Cross-region peer deviation, same units. Lower, because the peer set is
    # small (<= 7 values) and the estimate is correspondingly noisier.
    peer_z: float = 5.0
    # Minimum |z| a point needs under *either* test to be flagged at all.
    min_score: float = 5.0
    # Consecutive flagged minutes separated by no more than this stay one
    # event. One minute of recovery inside a fault is normal.
    merge_gap_minutes: int = 2
    # An excursion longer than this is treated as a baseline shift rather than
    # an incident. The scored incidents run 5-15 minutes.
    max_event_minutes: int = 45
    # Shorter than this is indistinguishable from sampling noise.
    min_event_minutes: int = 2
    # Ground truth is one fault per incident; requiring corroboration keeps
    # single-point jitter out of the submission.
    min_points: int = 2
    # A robust MAD of exactly zero (a constant series) makes every deviation
    # infinite. Floor the scale at this fraction of the series' own magnitude
    # so a constant-zero metric does not fire on a rounding artefact.
    scale_floor_ratio: float = 1e-3
    # Peer comparison needs at least this many regions reporting the same
    # series at the same minute before it is trusted.
    min_peers: int = 4
    # Peer z is capped: with 4-7 peers the scaled MAD is a coarse estimate.
    peer_z_cap: float = 60.0


def _scaled_mad(values: list[float], centre: float) -> float:
    """Median absolute deviation, scaled to be a standard-deviation estimate."""
    if not values:
        return 0.0
    deviations = [abs(value - centre) for value in values]
    return 1.4826 * median(deviations)


def _scale(values: list[float], centre: float, params: DetectorParams) -> float:
    """A robust scale that is never zero for a non-degenerate series."""
    mad = _scaled_mad(values, centre)
    if mad > 0:
        return mad
    # All-identical history: fall back to the mean magnitude so that a series
    # sitting at zero and then jumping to 100 still scores, while a series
    # sitting at zero and twitching to 1e-9 does not.
    mean_magnitude = sum(abs(value) for value in values) / len(values) if values else 0.0
    floor = max(mean_magnitude, 1e-12) * params.scale_floor_ratio
    return max(floor, 1e-12)


def _self_scores(
    points: list[tuple[datetime, float]],
    params: DetectorParams,
) -> dict[datetime, float]:
    """Deviation of each minute from the series' own median, in MAD units."""
    values = [value for _, value in points]
    centre = median(values)
    scale = _scale(values, centre, params)
    return {moment: abs(value - centre) / scale for moment, value in points}


def _peer_scores(
    series: dict[SeriesKey, list[tuple[datetime, float]]],
    params: DetectorParams,
) -> dict[SeriesKey, dict[datetime, float]]:
    """Deviation of each minute from the cross-region peers at that minute.

    Peers are the other regions reporting the *same* role, source, metric and
    scope. Comparing across regions rather than across time inside one series
    is what makes a step change visible: the faulted region leaves the band its
    peers still occupy, while its own 14-day history may have drifted more.
    """
    groups: dict[tuple[str, str, str, str], dict[str, list[tuple[datetime, float]]]] = {}
    for key, points in series.items():
        group = (key.role, key.source, key.metric, key.scope)
        groups.setdefault(group, {})[key.region] = points

    result: dict[SeriesKey, dict[datetime, float]] = {}
    for group, per_region in groups.items():
        if len(per_region) < params.min_peers + 1:
            continue
        # minute -> region -> value
        by_time: dict[datetime, dict[str, float]] = {}
        for region, points in per_region.items():
            for moment, value in points:
                by_time.setdefault(moment, {})[region] = value
        for region, points in per_region.items():
            scores: dict[datetime, float] = {}
            for moment, value in points:
                cohort = by_time.get(moment)
                if not cohort or len(cohort) < params.min_peers + 1:
                    continue
                others = [other for name, other in cohort.items() if name != region]
                if len(others) < params.min_peers:
                    continue
                centre = median(others)
                scale = _scaled_mad(others, centre)
                if scale <= 0:
                    mean_magnitude = sum(abs(item) for item in others) / len(others)
                    scale = max(mean_magnitude, 1e-12) * params.scale_floor_ratio
                score = abs(value - centre) / scale
                scores[moment] = min(score, params.peer_z_cap)
            if scores:
                key = SeriesKey(region, group[0], group[1], group[2], group[3])
                result[key] = scores
    return result


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

    peer = _peer_scores(series, params)
    detections: list[Detection] = []
    stats: dict[str, Any] = {"series_scored": 0, "flagged_points": 0, "events_raw": 0, "events_dropped": 0}

    for key, points in series.items():
        if allowed is not None and key.role not in allowed:
            continue
        if len(points) < params.min_points:
            continue
        stats["series_scored"] += 1
        self_score = _self_scores(points, params)
        peer_score = peer.get(key, {})

        # A point is flagged when it is extreme against *either* reference.
        flags: list[tuple[datetime, float, float]] = []
        for moment, value in points:
            mine = self_score.get(moment, 0.0)
            theirs = peer_score.get(moment, 0.0)
            best = max(mine, theirs)
            if best < params.min_score:
                continue
            # Each test individually needs to be convincing: a point that is
            # mildly off for both reasons is not an incident.
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


def _build_detection(
    key: SeriesKey,
    run: list[tuple[datetime, float, float]],
    params: DetectorParams,
) -> Detection | None:
    start = run[0][0]
    end = run[-1][0]
    duration_minutes = (end - start).total_seconds() / 60.0
    if duration_minutes < params.min_event_minutes:
        # A single flagged minute is only kept if it is a violent outlier;
        # one-off spikes across thousands of series are otherwise fatal to
        # precision, and precision multiplies the whole time-accuracy score.
        if len(run) < params.min_points and max(item[2] for item in run) < params.self_z * 2:
            return None
    peak = max(run, key=lambda item: item[2])
    # Sum over the run rewards both intensity and persistence, which is what
    # separates a real incident from a coincidental spike.
    score = sum(item[2] for item in run)
    return Detection(
        key=key,
        start=start,
        end=end,
        score=score,
        peak_time=peak[0],
        peak_magnitude=peak[2],
        n_points=len(run),
        points=run,
    )
