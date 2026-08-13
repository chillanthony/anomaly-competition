"""Create public time-sliced samples from processed observations.

The command copies every processed source for every region and filters rows only
by the published observation ranges. It does not inspect labels or answer files.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil


CASES = {
    "case_001": ("2026-07-28T12:34:34Z", "2026-07-28T12:57:54Z"),
    "case_002": ("2026-07-28T15:10:01Z", "2026-07-28T15:29:21Z"),
    "case_003": ("2026-07-28T17:27:17Z", "2026-07-28T17:45:54Z"),
}


def parse(value: str) -> datetime | None:
    value = value.strip().strip('"')
    if not value:
        return None
    try:
        item = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (item if item.tzinfo else item.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def row_time(row: dict[str, str]) -> datetime | None:
    for key in ("timestamp", "timestamp_utc", "minute_utc", "first_seen", "received_at"):
        if key in row:
            item = parse(row[key])
            if item is not None:
                return item
    return None


ROOT_RANGE = re.compile(r"^(\d{14})_(\d{14})$")


def root_coverage(path: Path) -> tuple[datetime, datetime] | None:
    """Read the published source-root range encoded by its data directory."""
    match = ROOT_RANGE.match(path.name)
    if not match:
        return None
    try:
        start = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        end = datetime.strptime(match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return start, end


def source_root_for_case(source: Path, start: datetime, end: datetime) -> Path:
    roots = []
    for path in sorted(source.iterdir()):
        if not path.is_dir():
            continue
        coverage = root_coverage(path)
        regions = [child for child in path.iterdir() if child.is_dir() and (child / "processed").is_dir()]
        if coverage and len(regions) == 8 and coverage[0] <= start and end <= coverage[1]:
            roots.append(path)
    if len(roots) != 1:
        raise SystemExit(
            f"expected exactly one complete source root covering {start.isoformat()}..{end.isoformat()}, "
            f"found {len(roots)}"
        )
    return roots[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ranges = {name: (parse(start), parse(end)) for name, (start, end) in CASES.items()}
    selected = {}
    for case, (case_start, case_end) in ranges.items():
        assert case_start is not None and case_end is not None
        selected[case] = source_root_for_case(args.source, case_start, case_end)
        case_output = args.output / case
        if case_output.exists():
            shutil.rmtree(case_output)
        (case_output / selected[case].name).mkdir(parents=True, exist_ok=True)

    # Read each source root once even when several public cases share it.
    for date_root in sorted(set(selected.values()), key=lambda path: path.name):
        cases = [case for case, root in selected.items() if root == date_root]
        regions = sorted(path for path in date_root.iterdir() if (path / "processed").is_dir())
        if len(regions) != 8:
            raise SystemExit(f"{date_root}: expected 8 region directories, found {len(regions)}")
        for region in regions:
            for source_file in sorted((region / "processed").iterdir()):
                targets = {}
                handles = {}
                writers = {}
                for case in cases:
                    target = args.output / case / date_root.name / region.name / "processed" / source_file.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    targets[case] = target
                if source_file.stat().st_size == 0:
                    for target in targets.values():
                        target.touch()
                    continue
                with source_file.open(newline="", encoding="utf-8-sig", errors="replace") as source_handle:
                    reader = csv.DictReader(source_handle)
                    if not reader.fieldnames:
                        for target in targets.values():
                            target.touch()
                        continue
                    for case, target in targets.items():
                        handle = target.open("w", newline="", encoding="utf-8")
                        handles[case] = handle
                        writers[case] = csv.DictWriter(handle, fieldnames=reader.fieldnames, extrasaction="ignore")
                        writers[case].writeheader()
                    for row in reader:
                        timestamp = row_time(row)
                        if timestamp is None:
                            continue
                        for case in cases:
                            case_start, case_end = ranges[case]
                            if case_start <= timestamp <= case_end:
                                writers[case].writerow(row)
                for handle in handles.values():
                    handle.close()
    print(f"created {len(CASES)} cases under {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
