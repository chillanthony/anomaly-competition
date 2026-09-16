"""Run the Baseline over the complete phase-one dataset."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
REQUIRED_CSV_PREFIXES = (
    "frr_syslog_events_",
    "interface_metrics_",
    "netflow_5tuple_minute_readable",
    "node_metrics_",
    "routing_metrics_",
    "scrape_health_",
    "traffic_flow_metrics",
)


def _phase1_data_directories(data_root: Path) -> list[Path]:
    if not data_root.is_dir():
        raise ValueError(f"data root is not a directory: {data_root}")

    sys.path.insert(0, str(REPO))
    from aiops_challenge_2026.config import load_public_config

    cities = load_public_config("network_elements")["cities"]
    directories = sorted(
        path for path in data_root.glob("*/*_data") if path.is_dir()
    )
    by_city = {
        city: [path for path in directories if path.name.startswith(f"{city}_")]
        for city in cities
    }
    invalid = {city: paths for city, paths in by_city.items() if len(paths) != 1}
    if invalid:
        details = ", ".join(
            f"{city}={len(paths)}" for city, paths in invalid.items()
        )
        raise ValueError(f"expected one phase-one data directory per city: {details}")

    for city, paths in by_city.items():
        names = {path.name for path in paths[0].glob("*.csv")}
        missing = [
            prefix
            for prefix in REQUIRED_CSV_PREFIXES
            if not any(name.startswith(prefix) for name in names)
        ]
        if missing:
            raise ValueError(f"{city}: missing CSV sources: {', '.join(missing)}")
    return [by_city[city][0] for city in cities]


def _read_predictions(path: Path, *, allow_unknown_category: bool) -> list[dict]:
    sys.path.insert(0, str(REPO))
    from aiops_challenge_2026.schema import validate_prediction

    records = []
    seen_ids = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"prediction line {line_number} is not valid JSON") from exc
        category = raw.get("fault_category") if isinstance(raw, dict) else None
        is_quick_unknown = allow_unknown_category and category == {
            "major_category": "unknown",
            "sub_category": "unknown",
        }
        record = validate_prediction(raw, allow_invalid_category=is_quick_unknown)
        prediction_id = record["prediction_id"]
        if prediction_id in seen_ids:
            raise ValueError(f"duplicate prediction_id: {prediction_id}")
        seen_ids.add(prediction_id)
        records.append(
            {key: value for key, value in record.items() if not key.startswith("_")}
        )
    if not records:
        raise ValueError("baseline produced no predictions")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Baseline inference over the phase-one dataset"
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=REPO / "dataset-phase-1",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument(
        "--model", default="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
    )
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()

    if args.max_events is not None and args.max_events < 1:
        parser.error("--max-events must be at least 1")
    if args.output.exists():
        raise SystemExit("output path already exists")
    try:
        data_directories = _phase1_data_directories(args.data_root)
    except ValueError as exc:
        raise SystemExit(f"invalid phase-one dataset: {exc}") from None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aiops_phase1_baseline_") as temporary:
        temporary_output = Path(temporary) / "predictions.jsonl"
        command = [
            sys.executable,
            str(REPO / "baseline" / "bian" / "run.py"),
            "--data-root",
            str(args.data_root),
            "--output",
            str(temporary_output),
            "--prediction-prefix",
            "phase1_",
        ]
        if args.max_events is not None:
            command.extend(["--max-events", str(args.max_events)])
        if args.use_llm:
            command.extend(["--use-llm", "--model", args.model])
        try:
            subprocess.run(command, cwd=REPO, check=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"phase-one Baseline inference failed with exit code {exc.returncode}"
            ) from None

        try:
            records = _read_predictions(
                temporary_output,
                allow_unknown_category=not args.use_llm,
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(f"invalid Baseline output: {exc}") from None
        output_staging = args.output.with_name(f".{args.output.name}.tmp")
        output_staging.write_bytes(temporary_output.read_bytes())
        os.replace(output_staging, args.output)

    print(json.dumps(
        {
            "cities": len(data_directories),
            "events": len(records),
            "mode": "bian_llm" if args.use_llm else "quick_validation",
            "output": str(args.output),
        }
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
