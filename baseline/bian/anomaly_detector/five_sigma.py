from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import csv
import math
from pathlib import Path
from typing import Any


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time(row: dict[str, str]) -> datetime | None:
    for key in ("timestamp", "timestamp_utc", "minute_utc", "first_seen"):
        value = row.get(key, "").strip().strip('"')
        if value:
            value = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(value)
                return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def _city(path: Path, aliases: dict[str, str]) -> str | None:
    for alias, city in aliases.items():
        if alias in path.as_posix().lower():
            return city
    return None


def _node_id(row: dict[str, str], city: str | None) -> str | None:
    raw = row.get("node") or row.get("node_key") or ""
    raw = raw.strip().strip('"').lower()
    role = next((token for token in ("br-1", "br-2", "cr-1", "cr-2", "traffic-vm", "service-vm-1", "service-vm-2", "service-vm-3", "fw") if token in raw), None)
    if role is None or city is None:
        return None
    return f"{city}-{role}"


def _numeric_fields(row: dict[str, str]) -> list[tuple[str, float]]:
    ignored = {"id", "port", "collector_port", "protocol", "src_port", "dst_port", "flow_record_count"}
    result = []
    for key, value in row.items():
        if key in ignored or key.endswith("_port") or key in {"timestamp", "timestamp_utc", "prometheus_sample_time_utc", "minute_utc"}:
            continue
        number = _number(value)
        if number is not None:
            result.append((key, number))
    return result[:32]


def detect(root: Path, aliases: dict[str, str], sigma: float = 5.0) -> list[dict[str, Any]]:
    series: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for path in sorted(root.rglob("*.csv")):
        # High-cardinality flow tuples are retained in the sample for users,
        # but are not a stable per-series statistical signal for this compact
        # generic detector.
        if path.parent.name != "processed" or "frr_syslog" in path.name or "netflow" in path.name:
            continue
        city = _city(path, aliases)
        try:
            handle = path.open(newline="", encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        with handle:
            reader = csv.DictReader(handle)
            for row in reader:
                timestamp = _time(row)
                node = _node_id(row, city)
                if timestamp is None or node is None:
                    continue
                source = path.stem.split("_")[0]
                for metric, value in _numeric_fields(row):
                    series[(node, f"{source}.{metric}")].append((timestamp, value))
    points: list[dict[str, Any]] = []
    for (node, metric), values in series.items():
        values.sort(key=lambda item: item[0])
        if len(values) < 4:
            continue
        baseline_count = max(3, int(len(values) * 0.2))
        baseline = [value for _, value in values[:baseline_count]]
        mean = sum(baseline) / len(baseline)
        variance = sum((value - mean) ** 2 for value in baseline) / len(baseline)
        std = math.sqrt(variance)
        for timestamp, value in values[baseline_count:]:
            if (std > 0 and abs(value - mean) > sigma * std) or (std == 0 and value != mean):
                points.append({"time": timestamp, "node": node, "metric": metric, "magnitude": abs(value - mean) / max(std, 1e-12)})
    points.sort(key=lambda item: item["time"])
    events: list[dict[str, Any]] = []
    for point in points:
        if not events or point["time"] - events[-1]["end"] > timedelta(seconds=120):
            events.append({"start": point["time"], "end": point["time"], "points": []})
        event = events[-1]
        event["end"] = max(event["end"], point["time"])
        event["points"].append(point)
    for event in events:
        event["end"] = event["end"] + timedelta(minutes=1)
        event["points"].sort(key=lambda item: (-item["magnitude"], item["node"], item["metric"]))
    return events
