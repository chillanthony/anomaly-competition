"""Convert detector events into BiAn candidate evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import timezone
from typing import Any


def _node(city: str, role: str) -> str:
    return f"{city}-{role}"


def public_topology(config: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {"node_id": _node(city, role), "city": city, "role": role}
        for city in config["cities"]
        for role in config["candidate_roles"]
    ]
    edges = []
    for city in config["cities"]:
        for relation in config["reference_topology_relations"]:
            edges.append({
                "source": _node(city, relation[0]),
                "target": _node(city, relation[1]),
                "relation": relation[2],
            })
    return {"directed": False, "nodes": nodes, "edges": edges}


def _role(node_id: str, cities: list[str]) -> str:
    for city in cities:
        prefix = city + "-"
        if node_id.startswith(prefix):
            return node_id[len(prefix):]
    return node_id.rsplit("-", 1)[-1]


def _evidence_id(node: str, point: dict[str, Any], index: int) -> str:
    return f"E-{index:05d}-{node}"


def build_event_context(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Build all public candidates; never use labels or an answer file."""
    points_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, point in enumerate(event.get("points", []), 1):
        points_by_node[point["node"]].append({
            "evidence_id": _evidence_id(point["node"], point, index),
            "timestamp_utc": point["time"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": point["metric"].split(".", 1)[0],
            "metric": point["metric"],
            "magnitude": round(float(point["magnitude"]), 5),
        })
    candidates = []
    for city in config["cities"]:
        for role in config["candidate_roles"]:
            node = _node(city, role)
            evidence = sorted(points_by_node.get(node, []), key=lambda x: (-x["magnitude"], x["timestamp_utc"]))[:config["preprocessing"]["max_evidence_per_candidate"]]
            magnitudes = [item["magnitude"] for item in evidence]
            candidates.append({
                "node_id": node,
                "city": city,
                "device_role": role,
                "evidence": evidence,
                "anomaly_count": len(points_by_node.get(node, [])),
                "max_magnitude": round(max(magnitudes, default=0.0), 5),
                "deterministic_score": round(min(1.0, 1.0 - 1.0 / (1.0 + sum(magnitudes))), 6),
            })
    candidates.sort(key=lambda x: (-x["deterministic_score"], -x["max_magnitude"], x["node_id"]))
    timeline = [
        {"timestamp_utc": item["timestamp_utc"], "node_id": node, "metric": item["metric"], "magnitude": item["magnitude"]}
        for node, values in points_by_node.items()
        for item in sorted(values, key=lambda x: (x["timestamp_utc"], -x["magnitude"]))[:4]
    ]
    timeline.sort(key=lambda x: (x["timestamp_utc"], -x["magnitude"], x["node_id"]))
    return {
        "candidates": candidates,
        "topology": public_topology(config),
        "timeline": timeline[:config["preprocessing"]["max_timeline_events"]],
        "window": {
            "start_time": event["start"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end_time": event["end"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }
