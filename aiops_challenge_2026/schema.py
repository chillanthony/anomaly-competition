"""Strict JSONL schema validation shared by the evaluator and examples."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable


class SchemaError(ValueError):
    """Raised when a public record does not conform to the schema."""


def parse_utc(value: Any, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{field} must be a non-empty ISO 8601 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SchemaError(f"{field} is not a valid ISO 8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SchemaError(f"{field} must include a UTC offset")
    utc = parsed.astimezone(timezone.utc)
    return utc


def _object(record: Any, name: str, required: Iterable[str], allowed: Iterable[str]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SchemaError(f"{name} must be an object")
    required_set, allowed_set = set(required), set(allowed)
    missing = required_set - record.keys()
    extra = set(record) - allowed_set
    if missing:
        raise SchemaError(f"{name} missing fields: {sorted(missing)}")
    if extra:
        raise SchemaError(f"{name} has unknown fields: {sorted(extra)}")
    return record


def validate_prediction(record: Any, *, allow_duplicate_top5: bool = False) -> dict[str, Any]:
    item = _object(record, "prediction", ("prediction_id", "start_time", "end_time", "root_cause_top5", "fault_category"),
                   ("prediction_id", "start_time", "end_time", "root_cause_top5", "fault_category"))
    if not isinstance(item["prediction_id"], str) or not item["prediction_id"]:
        raise SchemaError("prediction_id must be a non-empty string")
    start, end = parse_utc(item["start_time"], "start_time"), parse_utc(item["end_time"], "end_time")
    if end <= start:
        raise SchemaError("end_time must be after start_time")
    top5 = item["root_cause_top5"]
    if not isinstance(top5, list) or len(top5) != 5:
        raise SchemaError("root_cause_top5 must contain exactly five entries")
    seen: set[str] = set()
    for expected_rank, cause in enumerate(top5, 1):
        cause = _object(cause, "root_cause_top5 entry", ("rank", "network_element_id"), ("rank", "network_element_id"))
        if type(cause["rank"]) is not int or cause["rank"] != expected_rank:
            raise SchemaError("root_cause_top5 ranks must be exactly 1 through 5")
        node = cause["network_element_id"]
        if not isinstance(node, str) or not node:
            raise SchemaError("network_element_id must be a non-empty string")
        if node in seen and not allow_duplicate_top5:
            raise SchemaError("root_cause_top5 cannot contain duplicate network_element_id")
        seen.add(node)
    category = _object(item["fault_category"], "fault_category", ("major_category", "sub_category"), ("major_category", "sub_category"))
    if not all(isinstance(category[key], str) and category[key] for key in category):
        raise SchemaError("fault categories must be non-empty strings")
    result = {**item, "_start": start, "_end": end}
    if allow_duplicate_top5 and len(seen) != len(top5):
        result["_top5_duplicate"] = True
    return result


def _prediction_id(value: Any, line_number: int) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return f"__invalid_prediction_line_{line_number:06d}"


def normalize_prediction_for_evaluation(record: Any, line_number: int) -> dict[str, Any]:
    """Keep an event scorable when only its RCA/category module is malformed.

    The official file format remains strict through :func:`validate_prediction`.
    The CLI uses this defensive loader because AD is an independent module: a
    duplicate/invalid Top-5 or taxonomy object must not erase a valid interval.
    A record without a usable interval is retained as an unmatched FP so that a
    bad prediction cannot improve the score by disappearing silently.
    """
    if not isinstance(record, dict):
        return {
            "prediction_id": _prediction_id(None, line_number),
            "start_time": None,
            "end_time": None,
            "root_cause_top5": [],
            "fault_category": {"major_category": "", "sub_category": ""},
            "_invalid_core": True,
            "_invalid_rca": True,
            "_invalid_category": True,
            "_evaluation_tolerant": True,
        }

    item = dict(record)
    item["prediction_id"] = _prediction_id(item.get("prediction_id"), line_number)
    try:
        start = parse_utc(item.get("start_time"), "start_time")
        end = parse_utc(item.get("end_time"), "end_time")
        if end <= start:
            raise SchemaError("end_time must be after start_time")
        item["_start"], item["_end"] = start, end
        item["_invalid_core"] = False
    except SchemaError:
        item["_start"], item["_end"] = None, None
        item["_invalid_core"] = True

    top5 = item.get("root_cause_top5")
    try:
        checked = validate_prediction(
            {
                "prediction_id": item["prediction_id"],
                "start_time": "2000-01-01T00:00:00Z",
                "end_time": "2000-01-01T00:01:00Z",
                "root_cause_top5": top5,
                "fault_category": {"major_category": "_", "sub_category": "_"},
            },
            allow_duplicate_top5=True,
        )
        item["root_cause_top5"] = checked["root_cause_top5"]
        item["_invalid_rca"] = len(
            {entry["network_element_id"] for entry in top5}
        ) != 5
    except (SchemaError, TypeError, AttributeError):
        item["root_cause_top5"] = []
        item["_invalid_rca"] = True

    category = item.get("fault_category")
    if (
        isinstance(category, dict)
        and isinstance(category.get("major_category"), str)
        and category["major_category"]
        and isinstance(category.get("sub_category"), str)
        and category["sub_category"]
    ):
        item["fault_category"] = {
            "major_category": category["major_category"],
            "sub_category": category["sub_category"],
        }
        item["_invalid_category"] = False
    else:
        item["fault_category"] = {"major_category": "", "sub_category": ""}
        item["_invalid_category"] = True
    item["_evaluation_tolerant"] = True
    return item


def load_predictions_for_evaluation(path: Path) -> list[dict[str, Any]]:
    """Load prediction JSONL with module-level fault isolation for the CLI."""
    records: list[dict[str, Any]] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot open {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{path}:{line_number}: malformed JSONL") from exc
            records.append(normalize_prediction_for_evaluation(raw, line_number))
    seen: set[str] = set()
    for record in records:
        original = record["prediction_id"]
        if original in seen:
            record["prediction_id"] = f"{original}__line_{len(seen) + 1:06d}"
        seen.add(record["prediction_id"])
    return records


def validate_ground_truth(record: Any) -> dict[str, Any]:
    item = _object(record, "ground truth", ("ground_truth_id", "start_time", "end_time", "root_cause", "fault_category"),
                   ("ground_truth_id", "start_time", "end_time", "root_cause", "fault_category"))
    if not isinstance(item["ground_truth_id"], str) or not item["ground_truth_id"]:
        raise SchemaError("ground_truth_id must be a non-empty string")
    start, end = parse_utc(item["start_time"], "start_time"), parse_utc(item["end_time"], "end_time")
    if end <= start:
        raise SchemaError("end_time must be after start_time")
    root = _object(item["root_cause"], "root_cause", ("network_element_id",), ("network_element_id",))
    if not isinstance(root["network_element_id"], str) or not root["network_element_id"]:
        raise SchemaError("root_cause.network_element_id must be a non-empty string")
    category = _object(item["fault_category"], "fault_category", ("major_category", "sub_category"), ("major_category", "sub_category"))
    if not all(isinstance(category[key], str) and category[key] for key in category):
        raise SchemaError("fault categories must be non-empty strings")
    return {**item, "_start": start, "_end": end}


def load_jsonl(path: Path, validator) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot open {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"{path}:{line_number}: malformed JSONL") from exc
            try:
                records.append(validator(raw))
            except SchemaError as exc:
                raise SchemaError(f"{path}:{line_number}: {exc}") from exc
    return records


def ensure_unique(records: list[dict[str, Any]], field: str) -> None:
    values = [record[field] for record in records]
    if len(values) != len(set(values)):
        raise SchemaError(f"{field} values must be unique")
