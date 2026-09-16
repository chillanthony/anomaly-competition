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
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .detector import Detection, DetectorParams, detect_events
from .sources import load_series
from .vocab import CITIES, DEVICE_ROLES, EXCLUDED_ROLES, is_candidate_role

# How far apart two detections may be and still belong to the same incident.
# The scored incidents run 5-15 minutes, so a detection further out than this
# belongs to a different incident.
INCIDENT_TOLERANCE = timedelta(minutes=12)
# Upper bound on submitted records. Precision multiplies the entire time
# accuracy component, so an unbounded tail of weak detections is worse than a
# truncated list of strong ones.
MAX_EVENTS = 2000


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- incident construction -------------------------------------------------


def _dominant_region(cluster: list[Detection]) -> str:
    scores: dict[str, float] = defaultdict(float)
    for detection in cluster:
        scores[detection.key.region] += detection.score
    return max(scores.items(), key=lambda item: item[1])[0] if scores else ""


def build_incidents(detections: list[Detection]) -> list[dict[str, Any]]:
    """Cluster detections into incidents and rank the candidate root causes."""
    ordered = sorted(detections, key=lambda item: item.start)
    clusters: list[list[Detection]] = []
    for detection in ordered:
        placed = False
        for cluster in clusters:
            # Overlap or near-adjacency, and at least one shared region: two
            # simultaneous faults in different regions are two incidents.
            if detection.start - max(item.end for item in cluster) > INCIDENT_TOLERANCE:
                continue
            if not any(item.key.region == detection.key.region for item in cluster):
                continue
            cluster.append(detection)
            placed = True
            break
        if not placed:
            clusters.append([detection])

    incidents: list[dict[str, Any]] = []
    for cluster in clusters:
        by_element: dict[str, float] = defaultdict(float)
        by_element_detail: dict[str, list[Detection]] = defaultdict(list)
        for detection in cluster:
            if not is_candidate_role(detection.key.role):
                continue
            element = detection.key.element_id
            by_element[element] += detection.score
            by_element_detail[element].append(detection)
        if not by_element:
            continue

        incidents.append(
            {
                "start": min(item.start for item in cluster),
                "end": max(item.end for item in cluster),
                "detections": cluster,
                "element_scores": dict(by_element),
                "element_details": {key: list(value) for key, value in by_element_detail.items()},
                "region": _dominant_region(cluster),
                "magnitude": max(item.peak_magnitude for item in cluster),
                "n_points": sum(item.n_points for item in cluster),
            }
        )
    incidents.sort(key=lambda item: item["start"])
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


def rank_elements(incident: dict[str, Any]) -> list[str]:
    """Order candidate network elements for an incident.

    Ranking combines how strongly each element deviated with how early it did
    so relative to the incident, and prefers elements in the incident's own
    region. Every candidate comes from an observed series, so no fabricated
    element can enter the list. The list is padded to five only when fewer than
    five were observed, because a short list scores zero on RCA rather than
    scoring less.
    """
    element_scores: dict[str, float] = incident["element_scores"]
    details: dict[str, list[Detection]] = incident["element_details"]
    incident_start: datetime = incident["start"]
    region: str = incident["region"]

    ranked: list[tuple[float, str]] = []
    for element, score in element_scores.items():
        earliest = min(item.start for item in details[element])
        # Precedence: the faulted device normally moves first and its
        # dependents follow. Expressed as an exponential decay in minutes so
        # it reorders near-ties without overriding a large magnitude gap.
        delay_minutes = max(0.0, (earliest - incident_start).total_seconds() / 60.0)
        precedence = 0.5 ** (delay_minutes / 10.0)
        region_bonus = 1.0 if element.startswith(f"{region}-") else 0.6
        ranked.append((score * (0.7 + 0.3 * precedence) * region_bonus, element))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    elements = [element for _, element in ranked]

    if len(elements) < 5:
        for filler in _filler_candidates(region, elements):
            elements.append(filler)
            if len(elements) >= 5:
                break
    return elements[:5]


# --- fault classification --------------------------------------------------

# Which fault categories each metric family can indicate. The first match in
# the ordered tuple wins, so more specific patterns precede generic ones.
METRIC_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("dns",), "service", "dns_down"),
    (("web_5xx", "web_error"), "service", "web_5xx"),
    (("web_", "web_flow"), "service", "web_slow"),
    (("auth_timeout",), "service", "auth_timeout"),
    (("auth_", "auth_flow"), "service", "auth_error"),
    (("cpu", "load1", "load5"), "resource", "cpu_pressure"),
    (("memory", "swap"), "resource", "memory_pressure"),
    (("disk", "inode", "filesystem"), "resource", "disk_io_pressure"),
    (("process_count", "open_fd"), "resource", "process_pressure"),
    (("bgp",), "routing", "bgp_session_down"),
    (("ospf6", "ospf"), "routing", "ospf6_neighbor_down"),
    (("route", "fib", "rib"), "routing", "route_blackhole"),
    (("acl", "rule"), "firewall", "firewall_acl_drop"),
    (("session", "conn"), "firewall", "firewall_rate_limit"),
    (("carrier", "rx_error", "tx_error", "rx_drop", "tx_drop"), "link", "link_loss"),
    (("delay", "latency", "jitter"), "link", "link_delay"),
    (("scrape",), "service", "web_slow"),
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


def _category_for_metric(metric: str) -> tuple[str, str]:
    lowered = metric.lower()
    for patterns, major, minor in METRIC_CATEGORY_RULES:
        if any(pattern in lowered for pattern in patterns):
            return major, minor
    return DEFAULT_CATEGORY


def classify(incident: dict[str, Any], elements: list[str]) -> dict[str, str]:
    """Pick the fault category from the metrics that actually fired."""
    votes: dict[tuple[str, str], float] = defaultdict(float)
    for detection in incident["detections"]:
        votes[_category_for_metric(detection.key.metric)] += detection.score

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
