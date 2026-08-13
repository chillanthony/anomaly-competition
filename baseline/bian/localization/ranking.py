"""BiAn ranking stages and transparent Rank-of-Ranks aggregation."""

from __future__ import annotations

import math
from typing import Any


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in scores.values())
    if total <= 0:
        equal = 1.0 / len(scores) if scores else 0.0
        return {key: equal for key in scores}
    return {key: max(0.0, value) / total for key, value in scores.items()}


def validate_stage1(value: Any, nodes: list[str]) -> dict[str, Any]:
    # The reference implementation asks for a score list.  Some compatible
    # local checkpoints emit the same information as a node-keyed object;
    # accept that shape only when it still covers exactly the requested set.
    if isinstance(value, dict) and set(value) == {"scores"} and isinstance(value["scores"], list):
        items = value["scores"]
    elif isinstance(value, dict) and set(value) == set(nodes):
        items = []
        for node in nodes:
            item = value[node]
            if not isinstance(item, dict):
                raise ValueError("invalid Stage 1 node-keyed score")
            score = item.get("score", item.get("anomaly_score"))
            items.append({"node_id": node, "score": score})
    else:
        raise ValueError("Stage 1 must contain a scores list")
    scores = {}
    for item in items:
        if not isinstance(item, dict) or not {"node_id", "score"} <= set(item):
            raise ValueError("invalid Stage 1 score item")
        node, score = item["node_id"], item["score"]
        if node not in nodes or node in scores or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0:
            raise ValueError("invalid Stage 1 node or score")
        scores[node] = float(score)
    if set(scores) != set(nodes):
        raise ValueError("Stage 1 must score every candidate")
    return {"scores": _normalize(scores)}


def rank_stage1(candidates: list[dict[str, Any]], analyses: dict[str, dict[str, Any]], config: dict[str, Any], *, model_scores: dict[str, float] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = []
    for candidate in candidates:
        node = candidate["node_id"]
        model = float((model_scores or {}).get(node, analyses.get(node, {}).get("anomaly_score", 0.0)))
        evidence = float(candidate.get("deterministic_score", 0.0))
        raw = config["weights"]["model_anomaly_score"] * model + config["weights"]["deterministic_feature_score"] * evidence
        records.append({
            "node_id": node,
            "device_role": candidate["device_role"],
            "stage1_score": max(0.0, raw),
            "model_anomaly_score": model,
            "deterministic_feature_score": evidence,
            "supporting_evidence_ids": [item["evidence_id"] for item in candidate["evidence"][:12]],
            "evidence": candidate["evidence"],
        })
    normalized = _normalize({item["node_id"]: item["stage1_score"] for item in records})
    records.sort(key=lambda item: (-normalized[item["node_id"]], item["node_id"]))
    for item in records:
        item["stage1_score"] = normalized[item["node_id"]]
    shortlist = []
    cumulative = 0.0
    for item in records:
        shortlist.append(dict(item))
        cumulative += item["stage1_score"]
        if len(shortlist) >= config["min_candidates"] and cumulative >= config["top_p"]:
            break
        if len(shortlist) >= config["max_candidates"]:
            break
    for index, item in enumerate(shortlist, 1):
        item["candidate_id"] = f"C{index:02d}"
    return records, shortlist


def validate_stage2(value: Any, nodes: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"candidates"} or not isinstance(value["candidates"], list):
        raise ValueError("Stage 2 must contain candidates")
    seen = set()
    rendered = []
    for item in value["candidates"]:
        required = {"node_id", "local_anomaly_score", "temporal_precedence_score", "topology_upstream_score", "fault_pattern_compatibility_score", "symptom_likelihood", "reason"}
        if not isinstance(item, dict) or not required <= set(item):
            raise ValueError("invalid Stage 2 candidate schema")
        node = item["node_id"]
        if node not in nodes or node in seen:
            raise ValueError("invalid or duplicate Stage 2 candidate")
        seen.add(node)
        clean = {"node_id": node, "reason": item["reason"]}
        if not isinstance(clean["reason"], str):
            raise ValueError("Stage 2 reason must be text")
        for field in required - {"node_id", "reason"}:
            value_float = item[field]
            if not isinstance(value_float, (int, float)) or not math.isfinite(value_float) or not 0 <= value_float <= 1:
                raise ValueError(f"invalid Stage 2 component: {field}")
            clean[field] = float(value_float)
        rendered.append(clean)
    if set(seen) != set(nodes):
        raise ValueError("Stage 2 must score every shortlist candidate")
    return {"candidates": rendered}


def stage2_consensus(rounds: list[list[dict[str, Any]]], shortlist: list[dict[str, Any]], weights: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nodes = tuple(item["node_id"] for item in shortlist)
    rankings = []
    reasons = {}
    for values in rounds:
        scores = {}
        for item in values:
            scores[item["node_id"]] = max(0.0, sum(weights[field] * item[field] for field in weights))
            reasons.setdefault(item["node_id"], item["reason"])
        rankings.append(sorted(nodes, key=lambda node: (-scores[node], node)))
    rank_sums = {node: sum(ranking.index(node) + 1 for ranking in rankings) for node in nodes}
    ordered = sorted(nodes, key=lambda node: (rank_sums[node], node))
    inverse = _normalize({node: 1.0 / rank_sums[node] for node in ordered[:5]})
    # Reasons remain in the detailed rank data; public JSONL follows the
    # evaluator schema and contains only rank and network_element_id.
    rank_data = {"rounds": len(rounds), "raw_rankings": rankings, "average_ranks": {node: rank_sums[node] / len(rankings) for node in nodes}, "inverse_rank_scores": inverse, "reasons": reasons}
    top5 = [{"rank": index, "network_element_id": node} for index, node in enumerate(ordered[:5], 1)]
    return top5, rank_data


def quick_validation_top5(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: (-item.get("deterministic_score", item.get("stage1_score", 0.0)), -item.get("max_magnitude", 0.0), item["node_id"]))
    return [{"rank": index, "network_element_id": item["node_id"]} for index, item in enumerate(ordered[:5], 1)]
