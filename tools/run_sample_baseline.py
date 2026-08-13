"""Run the Baseline over all public sample cases without invoking evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
CASES = ("case_001", "case_002", "case_003")


def _read_jsonl(path: Path) -> list[dict]:
    sys.path.insert(0, str(REPO))
    from aiops_challenge_2026.config import load_public_config
    from aiops_challenge_2026.schema import validate_prediction

    network = load_public_config("network_elements")
    taxonomy = load_public_config("fault_taxonomy")
    valid_ids = {
        f"{city}-{role}"
        for city in network["cities"]
        for role in network["device_roles"]
    }
    valid_categories = {
        (sub_category.split("_", 1)[0], sub_category)
        for sub_category in taxonomy["sub_categories"]
    }
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = validate_prediction(json.loads(line))
        if any(
            cause["network_element_id"] not in valid_ids
            for cause in record["root_cause_top5"]
        ):
            raise ValueError("prediction contains an unknown network_element_id")
        category = record["fault_category"]
        pair = (category["major_category"], category["sub_category"])
        if pair not in valid_categories and pair != ("unknown", "unknown"):
            raise ValueError("prediction contains a category outside the public taxonomy")
        records.append(
            {key: value for key, value in record.items() if not key.startswith("_")}
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Baseline inference for the three public sample cases"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument(
        "--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
    )
    args = parser.parse_args()

    run_dir = args.run_dir or args.output.parent / f"{args.output.stem}_cases"
    if args.output.exists() or run_dir.exists():
        raise SystemExit("output path already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True)

    manifest = {
        "model": args.model if args.use_llm else None,
        "backend": "transformers" if args.use_llm else "quick_validation",
        "cases": [],
    }
    for case_name in CASES:
        case_output = run_dir / f"{case_name}.jsonl"
        command = [
            sys.executable,
            str(REPO / "baseline" / "bian" / "run.py"),
            "--data-root",
            str(REPO / "sample" / case_name),
            "--output",
            str(case_output),
            "--prediction-prefix",
            f"{case_name}_",
        ]
        if args.use_llm:
            command.extend(["--use-llm", "--model", args.model])
        subprocess.run(command, cwd=REPO, check=True)
        records = _read_jsonl(case_output)
        if len(records) != 1:
            raise RuntimeError(
                f"{case_name}: expected one prediction, got {len(records)}"
            )
        model_manifest_path = case_output.with_name(
            case_output.stem + ".manifest.json"
        )
        model_manifest = json.loads(
            model_manifest_path.read_text(encoding="utf-8")
        )
        if args.use_llm and model_manifest.get("mode") != "bian_llm":
            raise RuntimeError(
                f"{case_name}: model pipeline did not produce a valid prediction"
            )
        manifest["cases"].append(
            {
                "case": case_name,
                "prediction_file": case_output.name,
                "model_manifest": model_manifest_path.name,
            }
        )

    case_paths = [run_dir / f"{case_name}.jsonl" for case_name in CASES]
    staged_output = run_dir / ".combined.jsonl"
    staged_output.write_bytes(b"".join(path.read_bytes() for path in case_paths))
    records = _read_jsonl(staged_output)
    if len(records) != 3 or len({item["prediction_id"] for item in records}) != 3:
        raise RuntimeError("combined prediction must contain three unique events")
    os.replace(staged_output, args.output)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"events": 3, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
