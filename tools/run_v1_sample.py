#!/usr/bin/env python3
"""Offline regression: run the v1 detector on the labelled sample bundle.

The sample bundle is the only place ground truth is available offline, so it is
the gate that has to pass before anything is submitted: a detector that cannot
find three known incidents with clean spans will not find the real ones either.

Usage::

    python tools/run_v1_sample.py                       # all three cases
    python tools/run_v1_sample.py --case case_002       # one case
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The sample bundle splits ground truth across three short windows, one
# incident each, so each case is evaluated on its own.
CASES = ("case_001", "case_002", "case_003")
SAMPLE_RANGE = "20260728040000_20260729040000"


def _window_of(case_root: Path) -> tuple[str, str]:
    """The time span a sample case covers, read from its node_metrics file."""
    for path in sorted(case_root.rglob("node_metrics_*.csv")):
        first = last = None
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            header = handle.readline().rstrip("\n").split(",")
            if "timestamp" not in header:
                continue
            index = header.index("timestamp")
            for line in handle:
                parts = line.rstrip("\n").split(",")
                if len(parts) <= index:
                    continue
                if first is None:
                    first = parts[index]
                last = parts[index]
        if first and last:
            return first.strip(), last.strip()
    raise SystemExit(f"could not determine window for {case_root}")


def _overlaps(truth: dict, window: tuple[str, str]) -> bool:
    start, end = window
    return truth["start_time"] <= end and truth["end_time"] >= start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v1 detector against sample/ground_truth.jsonl")
    parser.add_argument("--case", action="append", choices=CASES, default=None)
    parser.add_argument("--sample-root", type=Path, default=REPO / "sample")
    parser.add_argument("--keep", action="store_true", help="keep the generated prediction files")
    parser.add_argument(
        "--detector-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="extra args passed through to baseline/v1/run.py",
    )
    args = parser.parse_args(argv)

    cases = args.case or list(CASES)
    truth_path = args.sample_root / "ground_truth.jsonl"
    if not truth_path.exists():
        print(f"missing {truth_path}", file=sys.stderr)
        return 2

    truths_all = [
        json.loads(line)
        for line in truth_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    overall_ok = True
    for case in cases:
        case_root = args.sample_root / case / SAMPLE_RANGE
        if not case_root.exists():
            print(f"[{case}] missing {case_root}", file=sys.stderr)
            overall_ok = False
            continue

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / f"{case}.jsonl"
            truth_subset = Path(tmp) / f"{case}.truth.jsonl"
            command = [
                sys.executable,
                "-m",
                "baseline.v1.run",
                "--data-root",
                str(case_root),
                "--output",
                str(out),
                *args.detector_args,
            ]
            print(f"[{case}] running detector")
            proc = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
            if proc.returncode != 0:
                print(proc.stdout)
                print(proc.stderr, file=sys.stderr)
                overall_ok = False
                continue
            print(proc.stdout.rstrip())

            predictions = [
                json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            window = _window_of(case_root)
            selected = [item for item in truths_all if _overlaps(item, window)]
            if not selected:
                print(f"[{case}] no ground truth inside {window[0]}..{window[1]}", file=sys.stderr)
                overall_ok = False
                continue
            truth_subset.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in selected) + "\n",
                encoding="utf-8",
            )
            print(
                f"[{case}] window {window[0]} .. {window[1]}; "
                f"{len(selected)} ground truth, {len(predictions)} predictions"
            )

            report = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aiops_challenge_2026.evaluator",
                    "--ground-truth",
                    str(truth_subset),
                    "--predictions",
                    str(out),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            print(report.stdout.rstrip())
            if report.stderr.strip():
                print(report.stderr.rstrip(), file=sys.stderr)
            if report.returncode != 0:
                overall_ok = False
            if args.keep:
                kept = REPO / "outputs" / f"sample-{case}.jsonl"
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"[{case}] kept {kept}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
