"""Generic extraction and normalization for JSON model responses.

Extraction is deliberately syntax-oriented.  Semantic acceptance remains the
responsibility of each stage validator; missing candidate information is never
invented from another stage or from deterministic rankings.
"""

from __future__ import annotations

import json
from typing import Any, Callable


class StructuredOutputError(ValueError):
    """Raised when no extracted JSON value satisfies the requested schema."""


def _balanced_tail_repair(fragment: str) -> Any:
    """Repair only complete values missing final container delimiters."""
    if fragment.count('"') % 2:
        raise json.JSONDecodeError("unterminated string", fragment, len(fragment))
    missing_arrays = fragment.count("[") - fragment.count("]")
    missing_objects = fragment.count("{") - fragment.count("}")
    if missing_arrays < 0 or missing_objects < 0 or not (missing_arrays or missing_objects):
        raise json.JSONDecodeError("not a repairable container tail", fragment, len(fragment))
    return json.loads(fragment + ("]" * missing_arrays) + ("}" * missing_objects))


def extract_json_values(text: str) -> list[Any]:
    """Return complete JSON objects/arrays found amid fences or prose."""
    answer = text.replace("```json", "").replace("```JSON", "").replace("```", "")
    decoder = json.JSONDecoder()
    values: list[Any] = []
    starts: list[int] = []
    for index, character in enumerate(answer):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(answer[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            values.append(value)
            starts.append(index)
    if not values:
        for index, character in enumerate(answer):
            if character not in "[{":
                continue
            try:
                value = _balanced_tail_repair(answer[index:].strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(value, (dict, list)):
                values.append(value)
                break
    return values


def _find_key(value: Any, key: str) -> list[Any]:
    matches: list[Any] = []
    if isinstance(value, dict):
        if key in value:
            matches.append(value[key])
        for child in value.values():
            matches.extend(_find_key(child, key))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_find_key(child, key))
    return matches


def normalize_json_value(value: Any, expected_key: str | None) -> list[Any]:
    """Produce equivalent wrapper forms without changing stage semantics."""
    normalized = [value]
    if expected_key is None:
        return normalized
    if isinstance(value, list):
        normalized.append({expected_key: value})
    for payload in _find_key(value, expected_key):
        normalized.append({expected_key: payload})
    return normalized


def parse_and_validate(
    text: str,
    *,
    expected_key: str | None,
    validator: Callable[[Any], Any],
) -> Any:
    """Validate the first semantically valid JSON representation in text."""
    values = extract_json_values(text)
    if not values:
        raise StructuredOutputError("model output contains no decodable JSON object or array")
    errors: list[str] = []
    for value in values:
        for normalized in normalize_json_value(value, expected_key):
            try:
                return validator(normalized)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    detail = errors[-1] if errors else "no compatible representation"
    raise StructuredOutputError(f"no extracted JSON value passed semantic validation: {detail}")
