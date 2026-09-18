"""CSV loading and series construction for the phase-1 dataset.

One pass per file, accumulating ``series key -> [(minute, value)]``. Two
directory layouts are supported transparently:

* ``<region>_<range>_data/<file>_<range>.csv``  -- the full dataset
* ``<city>_<range>/processed/<file>_<range>.csv`` -- the public sample bundle

Series identity differs per source because the generic
``(node, source.metric)`` key used by the shipped baseline silently merges
distinct interfaces and distinct routing labels into one series, which turns a
single flapping interface into a whole-node excursion. We key on the columns
that actually distinguish a time series in each source.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import csv
import math
from pathlib import Path
from typing import Any, Iterable, Iterator

from .vocab import region_of, role_of


# --- source identification -------------------------------------------------

# Match by substring so both ``node_metrics_20260728...csv`` and a bare
# ``node_metrics.csv`` resolve. Order matters: the first hit wins, so the more
# specific names are listed first.
SOURCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("interface_metrics", "interface_metrics"),
    ("traffic_flow_metrics", "traffic_flow"),
    ("node_metrics", "node_metrics"),
    ("routing_metrics", "routing_metrics"),
    ("scrape_health", "scrape_health"),
    ("frr_syslog", "frr_syslog"),
    ("netflow", "netflow"),
)

# Sources this detector actually models. frr_syslog is an event log with a few
# hundred rows over two weeks and netflow is tens of GB of 5-tuples; neither
# yields a stable per-minute numeric series at the granularity the score needs.
USABLE_SOURCES: frozenset[str] = frozenset(
    {"node_metrics", "interface_metrics", "routing_metrics", "scrape_health", "traffic_flow"}
)

_TIME_CANDIDATES = ("timestamp", "timestamp_utc", "minute_utc", "first_seen", "time")

# Non-measurement columns: identifiers, dimensions and nothing that a
# z-score could be taken of.
_NOT_MEASURED = frozenset(
    {
        "id",
        "target_id",
        "series_key",
        "flow_type",
        "source_ip",
        "protocol",
        "source_region",
        "target_region",
        "target_domain",
        "node_type",
        "if_role",
        "label",
        "metric_name",
        "node",
        "region",
        "interface_id",
        "exporter_type",
        "timestamp",
        "timestamp_utc",
        "minute_utc",
        "prometheus_sample_time_utc",
        "first_seen",
        "time",
    }
)


@dataclass(frozen=True)
class SeriesKey:
    """Identity of one time series.

    ``region`` and ``role`` are canonical; ``scope`` distinguishes the
    sub-entity within a device (interface name, routing label, exporter) and is
    empty when the metric is device-global.
    """

    region: str
    role: str
    source: str
    metric: str
    scope: str = ""

    @property
    def element_id(self) -> str:
        return f"{self.region}-{self.role}"


def _parse_time(value: str) -> datetime | None:
    text = (value or "").strip().strip('"')
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _number(value: str) -> float | None:
    text = (value or "").strip().strip('"')
    if not text or text == "\\N":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def identify_source(path: Path) -> str | None:
    name = path.name.lower()
    for pattern, source in SOURCE_PATTERNS:
        if pattern in name:
            return source
    return None


def _has_data_dir(path: Path) -> bool:
    parent = path.parent.name
    return parent == "processed" or parent.endswith("_data")


def discover_files(root: Path, sources: Iterable[str] | None = None) -> list[tuple[Path, str]]:
    """Every usable CSV under ``root`` as ``(path, source)``."""
    wanted = set(sources) if sources else set(USABLE_SOURCES)
    found: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.csv")):
        if not _has_data_dir(path):
            continue
        source = identify_source(path)
        if source is None or source not in wanted:
            continue
        found.append((path, source))
    return found


def _series_key(row: dict[str, str], source: str, region: str, role: str, metric: str) -> SeriesKey:
    """Attach the sub-entity scope that this source needs to stay one series."""
    if source == "interface_metrics":
        scope = (row.get("interface_id") or "").strip().strip('"')
    elif source == "routing_metrics":
        # label is a quoted Prometheus label set, e.g. command="bgp_summary";
        # without it every command collapses into one series.
        scope = f"{row.get('metric_name', '')}|{row.get('label', '')}".strip("|")
    elif source == "scrape_health":
        # One row per (target, exporter) pair.
        scope = f"{row.get('target_id', '')}|{row.get('exporter_type', '')}".strip("|")
    elif source == "traffic_flow":
        scope = (row.get("series_key") or "").strip().strip('"')
    else:
        scope = ""
    return SeriesKey(region=region, role=role, source=source, metric=metric, scope=scope)


def load_series(
    root: Path,
    *,
    regions: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    counters_as_rates: bool = True,
) -> tuple[dict[SeriesKey, list[tuple[datetime, float]]], dict[str, Any]]:
    """Read every usable CSV under ``root`` into per-series time/value lists.

    Returns ``(series, stats)`` where ``stats`` records how many files were
    skipped and why, so a run that silently finds nothing is diagnosable from
    its own log instead of looking like a healthy empty result.
    """
    stats: dict[str, Any] = {
        "files_read": 0,
        "files_skipped": 0,
        "rows_read": 0,
        "rows_dropped_unknown_identity": 0,
        "rows_dropped_unknown_region": 0,
        "series": 0,
        "points": 0,
        "per_source_rows": defaultdict(int),
        "per_source_series": defaultdict(int),
        "skipped_paths": [],
    }
    raw: dict[SeriesKey, list[tuple[datetime, float]]] = defaultdict(list)
    wanted_regions = set(regions) if regions else None
    for path, source in discover_files(root, sources):
        stats["files_read"] += 1
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                stats["rows_read"] += 1
                stats["per_source_rows"][source] += 1

                region = region_of(row.get("region", "")) or region_of(str(path))
                if region is None:
                    stats["rows_dropped_unknown_region"] += 1
                    continue
                if wanted_regions is not None and region not in wanted_regions:
                    continue

                role = role_of(row.get("node", ""), row.get("node_type", ""))
                if role is None:
                    stats["rows_dropped_unknown_identity"] += 1
                    continue

                moment = None
                for candidate in _TIME_CANDIDATES:
                    if row.get(candidate):
                        moment = _parse_time(row[candidate])
                        if moment is not None:
                            break
                if moment is None:
                    stats["rows_dropped_unknown_identity"] += 1
                    continue

                for column, value in row.items():
                    if column in _NOT_MEASURED or column.endswith("_port"):
                        continue
                    number = _number(value)
                    if number is None:
                        continue
                    key = _series_key(row, source, region, role, column)
                    raw[key].append((moment, number))

    # A cumulative counter is not comparable to itself across minutes; the
    # meaningful signal is its first difference. Detect by suffix rather than
    # by a hand-maintained list so new counters are covered automatically.
    series: dict[SeriesKey, list[tuple[datetime, float]]] = {}
    for key, points in raw.items():
        points.sort(key=lambda item: item[0])
        if counters_as_rates and (key.metric.endswith("_total") or key.metric.endswith("_count")):
            points = _diff_counter(points)
        if len(points) < MIN_POINTS_PER_SERIES:
            continue
        series[key] = points
        stats["per_source_series"][key.source] += 1

    stats["series"] = len(series)
    stats["points"] = sum(len(points) for points in series.values())
    stats["per_source_rows"] = dict(stats["per_source_rows"])
    stats["per_source_series"] = dict(stats["per_source_series"])
    return series, stats


# A series shorter than this cannot support a baseline plus a detection window:
# the opening baseline is 5 points and the shortest reportable event is 3, so
# anything under ~8 is unscoreable no matter what it contains. The bar sits a
# little above that to keep genuinely sparse series out, but not so high that a
# short case window is discarded wholesale -- the public sample bundle slices
# are only ~19-23 minutes long, and a 14-day threshold would silently drop two
# of its three cases.
MIN_POINTS_PER_SERIES = 12


def _diff_counter(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Convert a monotone counter into a per-minute delta series.

    A drop means the exporter restarted; that minute is dropped rather than
    recorded as a large negative spike, which would otherwise dominate every
    downstream magnitude.
    """
    result: list[tuple[datetime, float]] = []
    previous_time: datetime | None = None
    previous_value: float | None = None
    for moment, value in points:
        if previous_time is not None and previous_value is not None:
            gap_seconds = (moment - previous_time).total_seconds()
            # Only compare adjacent minutes; a hole in the series invalidates
            # the difference because the elapsed time is unknown.
            if 0 < gap_seconds <= 90 and value >= previous_value:
                result.append((moment, (value - previous_value) / (gap_seconds / 60.0)))
        previous_time, previous_value = moment, value
    return result
