from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from aiops_challenge_2026.evaluator.evaluator import evaluate
from aiops_challenge_2026.evaluator.__main__ import main as evaluator_main
from aiops_challenge_2026.schema import SchemaError, load_jsonl, validate_prediction


UTC = timezone.utc


def gt(start=0, end=100, root="n1", major="resource", sub="cpu"):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return {"ground_truth_id": "g1", "start_time": (base + timedelta(seconds=start)).isoformat(), "end_time": (base + timedelta(seconds=end)).isoformat(), "root_cause": {"network_element_id": root}, "fault_category": {"major_category": major, "sub_category": sub}}


def pred(start=0, end=100, nodes=None, major="resource", sub="cpu", pid="p1"):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    nodes = nodes or ["n1", "n2", "n3", "n4", "n5"]
    return {"prediction_id": pid, "start_time": (base + timedelta(seconds=start)).isoformat(), "end_time": (base + timedelta(seconds=end)).isoformat(), "root_cause_top5": [{"rank": i, "network_element_id": node} for i, node in enumerate(nodes, 1)], "fault_category": {"major_category": major, "sub_category": sub}}


def test_completely_correct():
    result = evaluate([gt()], [pred()])
    assert result["TP"] == 1 and result["FP"] == 0 and result["FN"] == 0
    assert result["Total"] == pytest.approx(100.0)


def test_small_time_error():
    result = evaluate([gt()], [pred(10, 110)])
    assert result["AD"] < 40 and result["AD"] > 0


def test_dice_below_threshold_is_fn_fp():
    result = evaluate([gt(0, 100)], [pred(100, 200)])
    assert (result["TP"], result["FP"], result["FN"]) == (0, 1, 1)


def test_one_gt_many_predictions_only_one_tp():
    result = evaluate([gt()], [pred(pid="p1"), pred(0, 90, pid="p2")])
    assert (result["TP"], result["FP"]) == (1, 1)


def test_one_prediction_can_match_only_one_gt():
    truths = [gt(root="n1"), {**gt(root="n2"), "ground_truth_id": "g2", "start_time": datetime(2026, 1, 1, tzinfo=UTC).isoformat(), "end_time": (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=100)).isoformat()}]
    result = evaluate(truths, [pred()])
    assert (result["TP"], result["FN"]) == (1, 1)


def test_fp_and_fn_and_zero_predictions():
    result = evaluate([gt()], [])
    assert (result["TP"], result["FP"], result["FN"], result["AD"]) == (0, 0, 1, 0.0)


@pytest.mark.parametrize("rank,expected", [(1, 1.0), (2, .8), (3, .6), (4, .4), (5, .2)])
def test_top5_rank_scores(rank, expected):
    nodes = [f"n{i}" for i in range(1, 6)]
    nodes[rank - 1] = "root"
    assert evaluate([gt(root="root")], [pred(nodes=nodes)]) ["RCA"] == pytest.approx(40 * expected)


def test_duplicate_top5_is_rejected_by_schema_but_scores_zero_rca():
    bad = pred(nodes=["n1", "n1", "n2", "n3", "n4"])
    with pytest.raises(SchemaError):
        validate_prediction(bad)
    assert evaluate([gt()], [bad])["RCA"] == 0


def test_major_gates_minor():
    result = evaluate([gt(major="resource", sub="cpu")], [pred(major="link", sub="cpu")])
    assert result["Major"] == 0 and result["Minor"] == 0


def test_malformed_jsonl(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="malformed"):
        load_jsonl(path, validate_prediction)


def test_invalid_time_and_missing_field():
    bad = pred()
    bad["start_time"] = "not-a-time"
    with pytest.raises(SchemaError):
        evaluate([gt()], [bad])
    bad = pred()
    del bad["fault_category"]
    with pytest.raises(SchemaError):
        evaluate([gt()], [bad])


def test_minor_requires_major_and_subcategory():
    result = evaluate([gt(major="resource", sub="cpu")], [pred(major="resource", sub="different")])
    assert result["Major"] == 10 and result["Minor"] == 0


def run_cli(tmp_path, prediction):
    truth_path = tmp_path / "truth.jsonl"
    prediction_path = tmp_path / "predictions.jsonl"
    report_path = tmp_path / "report.json"
    truth_path.write_text(json.dumps(gt()) + "\n", encoding="utf-8")
    prediction_path.write_text(json.dumps(prediction) + "\n", encoding="utf-8")
    old_argv = sys.argv
    try:
        sys.argv = ["evaluator", "--ground-truth", str(truth_path), "--predictions", str(prediction_path), "--report", str(report_path)]
        assert evaluator_main() == 0
    finally:
        sys.argv = old_argv
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_cli_duplicate_top5_keeps_ad_and_category_scores(tmp_path):
    bad = pred(nodes=["n1", "n1", "n2", "n3", "n4"])
    result = run_cli(tmp_path, bad)
    assert result["TP"] == 1
    assert result["AD"] == pytest.approx(40.0)
    assert result["RCA"] == pytest.approx(0.0)
    assert result["Major"] == pytest.approx(10.0)
    assert result["Minor"] == pytest.approx(10.0)


def test_cli_illegal_rank_keeps_ad_and_category_scores(tmp_path):
    bad = pred()
    bad["root_cause_top5"][0]["rank"] = 9
    result = run_cli(tmp_path, bad)
    assert result["AD"] == pytest.approx(40.0)
    assert result["RCA"] == pytest.approx(0.0)
    assert result["Major"] == pytest.approx(10.0)
    assert result["Minor"] == pytest.approx(10.0)


def test_cli_illegal_category_keeps_ad_and_rca(tmp_path):
    bad = pred()
    bad["fault_category"] = {"major_category": "resource"}
    result = run_cli(tmp_path, bad)
    assert result["AD"] == pytest.approx(40.0)
    assert result["RCA"] == pytest.approx(40.0)
    assert result["Major"] == pytest.approx(0.0)
    assert result["Minor"] == pytest.approx(0.0)


def test_cli_invalid_core_event_is_an_unmatched_fp(tmp_path):
    bad = pred()
    bad["start_time"] = "not-a-time"
    result = run_cli(tmp_path, bad)
    assert (result["TP"], result["FP"], result["FN"]) == (0, 1, 1)
    assert result["Total"] == pytest.approx(0.0)


def test_cli_invalid_prediction_id_is_normalized(tmp_path):
    bad = pred(pid="")
    result = run_cli(tmp_path, bad)
    assert result["TP"] == 1
    assert result["AD"] == pytest.approx(40.0)


def test_cli_malformed_prediction_jsonl_is_file_error(tmp_path):
    truth_path = tmp_path / "truth.jsonl"
    prediction_path = tmp_path / "predictions.jsonl"
    report_path = tmp_path / "report.json"
    truth_path.write_text(json.dumps(gt()) + "\n", encoding="utf-8")
    prediction_path.write_text("{not-json}\n", encoding="utf-8")
    old_argv = sys.argv
    try:
        sys.argv = ["evaluator", "--ground-truth", str(truth_path), "--predictions", str(prediction_path), "--report", str(report_path)]
        with pytest.raises(SchemaError, match="malformed JSONL"):
            evaluator_main()
    finally:
        sys.argv = old_argv
