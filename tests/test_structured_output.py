from __future__ import annotations

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baseline" / "bian"))

from localization.ranking import validate_stage2
from models.structured_output import StructuredOutputError, parse_and_validate
from models.backend import response_prefix_for


NODES = ["xian-br-1", "xian-cr-1"]


def candidate(node: str) -> dict:
    return {
        "node_id": node,
        "local_anomaly_score": 0.8,
        "temporal_precedence_score": 0.7,
        "topology_upstream_score": 0.6,
        "fault_pattern_compatibility_score": 0.5,
        "symptom_likelihood": 0.2,
        "reason": "observed evidence",
    }


def parse(text: str) -> dict:
    return parse_and_validate(
        text,
        expected_key="candidates",
        validator=lambda value: validate_stage2(value, NODES),
    )


STANDARD = '{"candidates":[' + ",".join(
    __import__("json").dumps(candidate(node)) for node in NODES
) + "]}"


@pytest.mark.parametrize(
    "text",
    [
        STANDARD,
        f"```json\n{STANDARD}\n```",
        f"Here is the ranking.\n{STANDARD}",
        f"{STANDARD}\nRanking complete.",
    ],
)
def test_common_json_wrappers_and_prose(text):
    assert [item["node_id"] for item in parse(text)["candidates"]] == NODES


def test_generic_nested_wrapper():
    assert len(parse('{"result":' + STANDARD + "}")["candidates"]) == 2


def test_bare_candidate_list():
    bare = "[" + ",".join(__import__("json").dumps(candidate(node)) for node in NODES) + "]"
    assert len(parse(bare)["candidates"]) == 2


def test_reasoning_json_before_valid_answer():
    text = '{"note":"intermediate"}\n' + STANDARD
    assert len(parse(text)["candidates"]) == 2


@pytest.mark.parametrize("text", ["not json", '{"candidates":[', '{"status":"done"}'])
def test_malformed_or_missing_candidates_fails(text):
    with pytest.raises(StructuredOutputError):
        parse(text)


def test_duplicate_candidate_fails():
    import json
    value = {"candidates": [candidate(NODES[0]), candidate(NODES[0])]}
    with pytest.raises(StructuredOutputError):
        parse(json.dumps(value))


def test_illegal_network_element_id_fails():
    import json
    value = {"candidates": [candidate(NODES[0]), candidate("unknown-node")]}
    with pytest.raises(StructuredOutputError):
        parse(json.dumps(value))


def test_schema_prefill_contains_no_candidate_or_label_value():
    assert response_prefix_for("7b_b_stage2") == '{"candidates":['
    assert "xian" not in response_prefix_for("7b_b_stage2")
    assert response_prefix_for("classification") == ""


def test_equivalent_candidate_with_extra_metadata_is_normalized():
    import json
    values = [candidate(node) for node in NODES]
    values[0]["device_role"] = "br-1"
    result = parse(json.dumps({"candidates": values}))
    assert "device_role" not in result["candidates"][0]
