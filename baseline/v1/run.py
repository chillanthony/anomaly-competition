"""CLI: detect, rank and classify phase-1 incidents into a submission JSONL.

Output contract (enforced by ``aiops_challenge_2026.schema.validate_prediction``)
is exactly five fields per record::

    {"prediction_id", "start_time", "end_time", "root_cause_top5", "fault_category"}

with ``root_cause_top5`` being ``[{"rank": i, "network_element_id": ...}]``
ranked 1..n with no repeats. A repeated element zeroes the whole RCA component,
so deduplication here is a scoring requirement, not tidiness.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import heapq
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .detector import Detection, DetectorParams, detect_events
from .sources import load_series
from .vocab import CITIES, DEVICE_ROLES, EXCLUDED_ROLES, is_candidate_role

# Incidents are chosen by scoring candidate windows directly, not by clustering
# detections. Clustering was the wrong shape for this problem: it chains, so a
# faint series at the opening minute and another at the close link into one
# cluster whose union spans the whole file, and every region then reports the
# same blob.
#
# Instead: slide a window of at most WINDOW_MAX_MINUTES over a per-minute grid,
# score each placement by how many *distinct* flagged elements it covers, and
# keep the winners after suppressing overlaps. The divisor is what stops the
# answer from always being "the whole file" -- a long window pays for its length.
# A square root is the right price: it is flat enough that a real 15-minute
# incident still beats a 5-minute echo, and steep enough that a 24-minute window
# must cover far more elements than a 12-minute one to score the same.
WINDOW_MAX_MINUTES = 24
WINDOW_SPAN_POWER = 0.5
# A window is worth reporting only if it scores within this fraction of the best
# one. Measured on the labelled sample: the winner leads the runner-up by ~7x, so
# anything from 0.2 to 1.0 selects the same single window -- the exact value is
# not load-bearing, but it must be a *relative* bar. An absolute one would either
# fire everywhere on a quiet 14-day file or nowhere on a busy one.
WINDOW_KEEP_RATIO = 0.2
# Upper bound on submitted records. Precision multiplies the entire time
# accuracy component, so an unbounded tail of weak detections is worse than a
# truncated list of strong ones. The cap keeps the *strongest* windows, not the
# earliest: on a 14-day file the incidents that matter are not the ones that
# happen to start on day one.
MAX_EVENTS = 2000
# Window placements retained as suppression input, per submitted record allowed.
# Suppression consumes a ladder of overlapping placements per incident, so it
# needs a generous multiple of the cap -- but the input must stay bounded, since
# a 14-day file has millions of placements and materialising them all costs far
# more than the answer is worth.
CANDIDATE_RESERVE = 8


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- incident construction -------------------------------------------------


def _minute_grid(detections: list[Detection]) -> list[datetime]:
    """Every minute spanned by the flagged points, inclusive.

    Built from the *points* rather than the padded event bounds so the grid
    tracks where evidence actually is. A minute with no flagged element is a
    legitimate cell in the grid -- it is what makes a gap expensive to span.
    """
    minutes = sorted({minute for detection in detections for minute, _, _ in detection.points})
    if not minutes:
        return []
    span = int((minutes[-1] - minutes[0]).total_seconds() // 60)
    return [minutes[0] + timedelta(minutes=offset) for offset in range(span + 1)]


def _flagged_by_minute(detections: list[Detection]) -> dict[datetime, set[str]]:
    """Per minute, which elements were flagged.

    Counting *distinct elements*, not detections or points: twenty metrics all
    spiking on one VM is one piece of evidence, not twenty. Corroboration across
    many elements is what a real incident looks like.
    """
    flagged: dict[datetime, set[str]] = defaultdict(set)
    for detection in detections:
        if not is_candidate_role(detection.key.role):
            continue
        for minute, _, _ in detection.points:
            flagged[minute].add(detection.key.element_id)
    return flagged


def _candidate_windows(
    grid: list[datetime],
    flagged: dict[datetime, set[str]],
    *,
    keep: int,
) -> list[tuple[float, datetime, datetime]]:
    """The strongest window placements, best first.

    A placement's score is the distinct elements it covers divided by
    ``span ** WINDOW_SPAN_POWER``. A prefix sum makes each placement O(1), and
    only a minute that flagged something can begin a best window -- a window
    starting on a silent minute is strictly improved by dropping that minute --
    so the enumeration is linear in evidence rather than in file length.

    Only the top ``keep`` placements are retained. They are the raw material for
    suppression, which needs a handful of candidates per incident, not a ranking
    of every placement. A 14-day file has millions of placements; dropping the
    weak ones at the source is what keeps this affordable. Ties are broken
    toward the earlier placement so the output does not depend on heap order.
    """
    if not grid:
        return []
    running = 0
    prefix = [0]
    active: list[bool] = []
    for minute in grid:
        cell = flagged.get(minute)
        running += len(cell) if cell else 0
        prefix.append(running)
        active.append(bool(cell))

    heaped: list[tuple[float, int, int]] = []
    for first, present in enumerate(active):
        if not present:
            continue
        for last in range(first, min(first + WINDOW_MAX_MINUTES, len(grid))):
            span = last - first + 1
            score = (prefix[last + 1] - prefix[first]) / span**WINDOW_SPAN_POWER
            entry = (score, -first, last)
            if len(heaped) < keep:
                heapq.heappush(heaped, entry)
            elif entry > heaped[0]:
                heapq.heapreplace(heaped, entry)
    heaped.sort(key=lambda item: (-item[0], -item[1]))
    return [(score, grid[-first], grid[last]) for score, first, last in heaped]


def _select_windows(
    candidates: list[tuple[float, datetime, datetime]],
    *,
    limit: int,
) -> list[tuple[float, datetime, datetime]]:
    """Greedy non-maximum suppression over candidate windows.

    A long window overlaps every short one inside it, and one incident produces a
    whole ladder of overlapping placements, so overlapping candidates describe
    the same incident and only the best survives.

    Suppression is global, and that is a deliberate limitation: two simultaneous
    incidents in *different* regions share the same minutes, so the stronger one
    suppresses the weaker and only one is submitted. Selecting per region instead
    was measured on the labelled sample and is far worse -- the sample's blast
    radius reaches all eight cities, so per-region selection emits eight
    near-identical full-width windows and one of them is the wrong answer. The
    global rule loses the rarer case (simultaneous cross-region faults) and wins
    the common one, which is the right way round for a precision-weighted score.

    ``candidates`` is ordered best-first, so the ratio bar cuts off a suffix and
    the loop can stop there. ``limit`` is the submission cap: the tail past it is
    dropped rather than submitted, because precision multiplies the whole time
    accuracy component and a weak record costs more than it earns.
    """
    if not candidates:
        return []
    cutoff = candidates[0][0] * WINDOW_KEEP_RATIO
    kept: list[tuple[float, datetime, datetime]] = []
    for candidate in candidates:
        if candidate[0] < cutoff or len(kept) >= limit:
            break
        _, start, end = candidate
        if not any(not (end < other_start or start > other_end) for _, other_start, other_end in kept):
            kept.append(candidate)
    kept.sort(key=lambda item: item[1])
    return kept


def _dominant_region(cluster: list[Detection]) -> str:
    """The region holding the most deviation mass in a cluster.

    Only a fallback for the empty-cluster edge case; the region that matters for
    ranking comes from the anchor's own element.
    """
    scores: dict[str, float] = defaultdict(float)
    for detection in cluster:
        scores[detection.key.region] += detection.score
    return max(scores.items(), key=lambda item: item[1])[0] if scores else ""


def build_incidents(detections: list[Detection], *, limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    """Turn detections into incidents: one window plus its candidate root causes."""
    grid = _minute_grid(detections)
    flagged = _flagged_by_minute(detections)
    selected = _select_windows(
        _candidate_windows(grid, flagged, keep=limit * CANDIDATE_RESERVE),
        limit=limit,
    )

    # Element -> its detections, each detection indexed by the grid positions its
    # points fall on. Membership testing a window then costs the detections of
    # the elements flagged inside it, and each test is a slice comparison against
    # a precomputed span instead of a scan of every point the detection holds.
    by_element_span: dict[str, list[tuple[int, int, Detection]]] = defaultdict(list)
    position = {minute: index for index, minute in enumerate(grid)}
    for detection in detections:
        if not is_candidate_role(detection.key.role):
            continue
        indices = [position[minute] for minute, _, _ in detection.points if minute in position]
        if indices:
            by_element_span[detection.key.element_id].append((min(indices), max(indices), detection))

    incidents: list[dict[str, Any]] = []
    for score, start, end in selected:
        first, last = position[start], position[end]
        elements = {element for minute in grid[first : last + 1] for element in flagged.get(minute, ())}
        # A detection belongs to the window when its own span overlaps it. Using
        # the span rather than the point set is exact here because the points are
        # on the grid, so overlapping spans is equivalent to sharing a minute.
        cluster = [
            detection
            for element in elements
            for low, high, detection in by_element_span.get(element, ())
            if low <= last and high >= first
        ]
        if not cluster:
            continue

        by_element: dict[str, float] = defaultdict(float)
        cluster_detail: dict[str, list[Detection]] = defaultdict(list)
        for detection in cluster:
            by_element[detection.key.element_id] += detection.score
            cluster_detail[detection.key.element_id].append(detection)

        incidents.append(
            {
                "start": start,
                "end": end,
                "detections": cluster,
                "element_scores": dict(by_element),
                "element_details": {key: list(value) for key, value in cluster_detail.items()},
                "region": _dominant_region(cluster),
                "score": score,
                "magnitude": max(item.peak_magnitude for item in cluster),
                "n_points": sum(item.n_points for item in cluster),
            }
        )
    return incidents


# --- root cause ranking ----------------------------------------------------


def _filler_candidates(region: str, already: list[str]) -> list[str]:
    """Unobserved in-region elements, most likely to be the faulted one first."""
    preferred = (
        "service-vm-1",
        "service-vm-2",
        "service-vm-3",
        "fw",
        "cr-1",
        "cr-2",
        "br-1",
        "br-2",
        "traffic-vm",
    )
    seen = set(already)
    for role in preferred:
        if not is_candidate_role(role):
            continue
        element = f"{region}-{role}"
        if element not in seen:
            yield element


def _element_weight(details: dict[str, list[Detection]]) -> dict[str, float]:
    """Per element, total deviation times the square root of distinct metrics.

    The count matters because a device that deviates on nine metrics and one that
    deviates hard on a single metric look alike once scores are summed, but only
    the first is a device under real stress -- a lone metric can be noise. The
    square root keeps corroboration a tiebreaker rather than the whole answer, so
    a single overwhelming deviation still outranks a broad mild one.
    """
    return {
        element: sum(item.score for item in detections) * len({item.key.metric for item in detections}) ** 0.5
        for element, detections in details.items()
    }


def _precedence(incident_start: datetime, detections: list[Detection]) -> float:
    """How much earlier than the incident the element moved, as a weight in [0.7, 1].

    The faulted device normally moves first and its dependents follow. Expressed
    as an exponential decay in minutes, then floored well above zero so it
    reorders near-ties without ever overriding a large magnitude gap.
    """
    earliest = min(item.start for item in detections)
    delay_minutes = max(0.0, (earliest - incident_start).total_seconds() / 60.0)
    return 0.7 + 0.3 * 0.5 ** (delay_minutes / 10.0)


def _anchor(details: dict[str, list[Detection]], start: datetime) -> str | None:
    """The single element best supported by evidence, or None if there is none.

    This is the window's answer before any preference is applied, and its region
    is the region the incident is treated as belonging to. Region cannot be read
    off the cluster as a whole: the cluster is a cross-region blast radius, and
    the region holding the most detections is merely the one with the most
    background activity, not the one containing the fault.
    """
    weights = _element_weight(details)
    if not weights:
        return None
    return max(weights, key=lambda element: (weights[element] * _precedence(start, details[element]), element))


def rank_elements(incident: dict[str, Any]) -> list[str]:
    """Order candidate network elements for an incident.

    Ranking is evidence first, preference second. The anchor -- the element with
    the strongest weighted deviation -- is the answer, and the region preference
    only orders the remaining four: the faulted device is usually in the incident
    region rather than in whatever region its blast radius reached. Keeping the
    anchor pinned matters because the region bonus is a 1.67x swing, large enough
    for a wrong region to overturn the strongest evidence in the window.

    Every candidate comes from an observed series, so no fabricated element can
    enter the list. The list is padded to five only when fewer than five were
    observed, because a short list scores zero on RCA rather than scoring less.
    """
    details: dict[str, list[Detection]] = incident["element_details"]
    start: datetime = incident["start"]
    anchor = _anchor(details, start)
    region = anchor.split("-")[0] if anchor else incident["region"]

    weights = _element_weight(details)
    ranked = sorted(
        weights,
        key=lambda element: (
            -weights[element]
            * _precedence(start, details[element])
            * (1.0 if element.startswith(f"{region}-") else 0.6),
            element,
        ),
    )
    if anchor is not None:
        ranked.remove(anchor)
        ranked.insert(0, anchor)

    if len(ranked) < 5:
        for filler in _filler_candidates(region, ranked):
            ranked.append(filler)
            if len(ranked) >= 5:
                break
    return ranked[:5]


# --- fault classification --------------------------------------------------

# Which fault categories each metric family can indicate. The first match in
# the ordered tuple wins, so more specific patterns precede generic ones.
#
# The second and third elements must be a pair that exists in
# ``fault_taxonomy.json``. They are *sub-category* names ("loss"), not fault
# names ("link_loss"); the taxonomy lists both and only the sub-category is
# scored. A pair that is not in the taxonomy is not an error here -- it is
# silently scored as zero -- so ``tools/check_categories.py`` asserts every
# rule against the config.
METRIC_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("dns",), "service", "dns_down"),
    (("web_5xx", "web_error"), "service", "web_5xx"),
    (("web_", "web_flow"), "service", "web_slow"),
    (("auth_timeout",), "service", "auth_timeout"),
    (("auth_", "auth_flow"), "service", "auth_error"),
    (("cpu", "load1", "load5"), "resource", "cpu_pressure"),
    (("memory", "swap"), "resource", "memory_pressure"),
    # Disk *fullness* and disk *throughput* are distinct faults upstream
    # (``resource_disk_space_low`` vs ``resource_disk_io_pressure``), so the
    # space metrics must be matched before the generic ``disk`` pattern takes
    # them. Order inside this tuple is load-bearing.
    (("filesystem", "inode"), "resource", "disk_space_low"),
    (("disk",), "resource", "disk_io_pressure"),
    (("process_count", "open_fd"), "resource", "process_pressure"),
    (("bgp",), "routing", "bgp_session_down"),
    (("ospf6", "ospf"), "routing", "ospf6_neighbor_down"),
    (("route", "fib", "rib"), "routing", "blackhole"),
    (("acl", "rule"), "firewall", "acl_drop"),
    (("session", "conn"), "firewall", "rate_limit"),
    (("carrier", "rx_error", "tx_error", "rx_drop", "tx_drop"), "link", "loss"),
    (("delay", "latency", "jitter"), "link", "delay"),
    (("scrape",), "service", "web_slow"),
)

# Metrics that carry no diagnostic weight and must therefore cast no vote.
#
# Interface throughput and the routing ``value`` column are *effects*: a busy
# interface is what a CPU, memory or disk fault looks like from outside, and
# ``value`` is whatever the routing exporter reported. Left in, they fall
# through to DEFAULT_CATEGORY and vote for cpu_pressure on every incident they
# appear in. On the labelled sample that is decisive -- case_002's strongest
# single detection is ``rx_bytes_rate`` at 21.5, which outweighs the genuine
# ``memory_available_ratio`` signal at 14.5 and flips the answer. An explicit
# drop list makes the omission deliberate rather than an accident of pattern
# matching.
NON_DIAGNOSTIC_METRICS: frozenset[str] = frozenset(
    {
        "rx_bytes_rate",
        "tx_bytes_rate",
        "rx_packets_rate",
        "tx_packets_rate",
        "value",
    }
)

# Which device roles can physically host which major category. Used to pick a
# category consistent with the ranked root cause rather than with a stray
# metric reported by a downstream node.
ROLE_MAJOR_AFFINITY: dict[str, tuple[str, ...]] = {
    "service-vm-1": ("resource", "service"),
    "service-vm-2": ("resource", "service"),
    "service-vm-3": ("resource", "service"),
    "fw": ("firewall", "resource", "link"),
    "cr-1": ("routing", "link", "resource"),
    "cr-2": ("routing", "link", "resource"),
    "br-1": ("link", "routing", "resource"),
    "br-2": ("link", "routing", "resource"),
    "traffic-vm": ("service", "link", "resource"),
    "monitor-vm": ("resource",),
}

# Fallback when the metrics point nowhere useful: the most common incident
# shape on a service VM in this dataset.
DEFAULT_CATEGORY = ("resource", "cpu_pressure")


def _category_for_metric(metric: str) -> tuple[str, str] | None:
    """Category implied by one metric, or ``None`` if it implies nothing.

    ``None`` is not "unknown, guess something" -- it means this metric carries
    no evidence about the fault class and must be left out of the vote.
    """
    lowered = metric.lower()
    if lowered in NON_DIAGNOSTIC_METRICS:
        return None
    for patterns, major, minor in METRIC_CATEGORY_RULES:
        if any(pattern in lowered for pattern in patterns):
            return major, minor
    return None


def classify(incident: dict[str, Any], elements: list[str]) -> dict[str, str]:
    """Pick the fault category from the metrics that actually fired.

    Each candidate category is scored by its *strongest single* detection, not
    by the sum over all of them. Summing rewards whichever family happens to
    report the most metrics, and the metric families here have wildly different
    units -- one busy interface counter on case_002 swings by 2e5 where the
    real memory signal moves 8%, so a sum is decided by the unit scale rather
    than by the fault. The max keeps a loud-but-irrelevant family from burying
    the one metric that is actually diagnostic.
    """
    votes: dict[tuple[str, str], float] = {}
    for detection in incident["detections"]:
        category = _category_for_metric(detection.key.metric)
        if category is None:
            continue
        if detection.score > votes.get(category, float("-inf")):
            votes[category] = detection.score

    if not votes:
        major, minor = DEFAULT_CATEGORY
        return {"major_category": major, "sub_category": minor}

    top_element = elements[0] if elements else ""
    top_role = top_element.split("-", 1)[1] if "-" in top_element else ""
    affinity = ROLE_MAJOR_AFFINITY.get(top_role, ())

    # A category the leading device cannot host is most likely a symptom
    # reported by a downstream node, so it is demoted rather than dropped.
    def weight(item: tuple[tuple[str, str], float]) -> float:
        (major, _), score = item
        if affinity and major not in affinity:
            return score * 0.35
        return score

    (major, minor), _ = max(votes.items(), key=lambda item: (weight(item), item[0]))
    return {"major_category": major, "sub_category": minor}


# --- record emission -------------------------------------------------------


def _load_config() -> dict[str, Any]:
    """Cities, roles and the taxonomy, read from the public config files.

    Single source of truth: if a later phase adds roles or categories, only the
    ``aiops_challenge_2026`` config changes.
    """
    try:
        from aiops_challenge_2026.config import load_public_config

        elements = load_public_config("network_elements")
        taxonomy = load_public_config("fault_taxonomy")
        cities = tuple(elements.get("cities", CITIES))
        roles = tuple(elements.get("device_roles", DEVICE_ROLES))
        pairs = {
            (item["major_category"], item["sub_category"])
            for item in taxonomy.get("fault_categories", [])
        }
    except Exception:  # pragma: no cover - config ships with this repo
        cities, roles, pairs = CITIES, DEVICE_ROLES, set()
    return {"cities": cities, "roles": roles, "category_pairs": pairs}


def to_records(
    incidents: list[dict[str, Any]],
    *,
    prefix: str = "v1-",
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = config or _load_config()
    valid_pairs = config["category_pairs"]
    records: list[dict[str, Any]] = []
    for index, incident in enumerate(incidents):
        # Deduplicate defensively: a repeated element invalidates the whole
        # RCA component for this prediction.
        seen: set[str] = set()
        unique: list[str] = []
        for element in rank_elements(incident):
            if element in seen:
                continue
            seen.add(element)
            unique.append(element)
        category = classify(incident, unique)
        if valid_pairs and (category["major_category"], category["sub_category"]) not in valid_pairs:
            category = {"major_category": DEFAULT_CATEGORY[0], "sub_category": DEFAULT_CATEGORY[1]}
        records.append(
            {
                "prediction_id": f"{prefix}{index:06d}",
                "start_time": _utc(incident["start"]),
                "end_time": _utc(incident["end"]),
                "root_cause_top5": [
                    {"rank": rank, "network_element_id": element}
                    for rank, element in enumerate(unique, start=1)
                ],
                "fault_category": dict(category),
            }
        )
    return records


# --- CLI -------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="v1 detector -> submission JSONL")
    parser.add_argument("--data-root", type=Path, required=True, help="dataset-phase-1 or a sample case directory")
    parser.add_argument("--output", type=Path, required=True, help="prediction JSONL path")
    parser.add_argument("--stats", type=Path, default=None, help="optional path for a JSON run summary")
    parser.add_argument("--regions", nargs="*", default=None, help="restrict to these region slugs")
    parser.add_argument("--sources", nargs="*", default=None, help="restrict to these sources")
    parser.add_argument("--prediction-prefix", default="v1-")
    parser.add_argument("--min-score", type=float, default=DetectorParams.min_score)
    parser.add_argument("--self-z", type=float, default=DetectorParams.self_z)
    parser.add_argument("--peer-z", type=float, default=DetectorParams.peer_z)
    parser.add_argument("--min-points", type=int, default=DetectorParams.min_points)
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS)
    parser.add_argument("--emit-all", action="store_true", help="emit every incident without the cap")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    params = DetectorParams(
        min_score=args.min_score,
        self_z=args.self_z,
        peer_z=args.peer_z,
        min_points=args.min_points,
    )

    series, load_stats = load_series(args.data_root, regions=args.regions, sources=args.sources)
    if not series:
        print(json.dumps({"error": "no series loaded", "load": _jsonable(load_stats)}, ensure_ascii=False, indent=2))
        return 2

    allowed_roles = [role for role in DEVICE_ROLES if role not in EXCLUDED_ROLES]
    detections, detect_stats = detect_events(series, params=params, roles=allowed_roles)
    incidents = build_incidents(detections)
    if not args.emit_all:
        incidents = incidents[: args.max_events]

    records = to_records(incidents, prefix=args.prediction_prefix, config=_load_config())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "data_root": str(args.data_root),
        "series": load_stats["series"],
        "points": load_stats["points"],
        "per_source_series": load_stats["per_source_series"],
        "rows_dropped_unknown_identity": load_stats["rows_dropped_unknown_identity"],
        "rows_dropped_unknown_region": load_stats["rows_dropped_unknown_region"],
        "series_scored": detect_stats["series_scored"],
        "flagged_points": detect_stats["flagged_points"],
        "events": len(detections),
        "incidents": len(incidents),
        "records": len(records),
        "params": params.__dict__,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.stats:
        args.stats.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _jsonable(stats: dict[str, Any]) -> dict[str, Any]:
    """defaultdict is not JSON-serialisable; flatten it for the error path."""
    return {key: (dict(value) if isinstance(value, defaultdict) else value) for key, value in stats.items()}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
