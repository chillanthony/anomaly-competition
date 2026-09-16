#!/usr/bin/env python3

import csv
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error


# 提交答案服务域名或 IP
JUDGE_SERVER = "https://judge.aiops.cn"
# 比赛 ID，可通过比赛页面 URL 获取
CONTEST = "2087843807868489822"
# 团队 ID，需要在参加比赛并组队后能获得，具体在比赛详情页-> 团队 -> 团队ID，为一串数字标识。
TICKET = "2099381749812301890"
SUBMISSIONS_LOG = Path(__file__).with_name("submissions.csv")

# Fields filled in by check_status(), in column order after "submission_id".
STATUS_FIELDS = (
    "score",
    "ad_score",
    "rca_score",
    "major_score",
    "minor_score",
    "judge_time",
    "error",
    "checked_at",
)
CSV_FIELDS = ("submitted_at", "submission_id") + STATUS_FIELDS


def _blank_row():
    return {field: "" for field in CSV_FIELDS}


def _read_history():
    """Load the local history, padding rows written by older versions."""
    if not SUBMISSIONS_LOG.exists() or SUBMISSIONS_LOG.stat().st_size == 0:
        return []
    with SUBMISSIONS_LOG.open("r", newline="", encoding="utf-8") as file:
        return [
            {field: (row.get(field) or "") for field in CSV_FIELDS}
            for row in csv.DictReader(file)
        ]


def _write_history(rows):
    tmp_path = SUBMISSIONS_LOG.with_name(SUBMISSIONS_LOG.name + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(SUBMISSIONS_LOG)


def _record_submission(submission_id):
    """Append a successful submission to the local CSV history."""
    try:
        rows = _read_history()
        row = _blank_row()
        row["submitted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row["submission_id"] = str(submission_id)
        rows.append(row)
        _write_history(rows)
    except OSError as exc:
        print("Warning: submission succeeded but history was not written: %s" % exc)


def _record_status(status):
    """Write status-API fields into the row of the matching submission id."""
    submission_id = str(status.get("submission_id") or "")
    if not submission_id:
        return
    try:
        rows = _read_history()
        for row in rows:
            if row["submission_id"] == submission_id:
                break
        else:
            row = _blank_row()
            row["submission_id"] = submission_id
            rows.append(row)
        for field in STATUS_FIELDS:
            if field == "checked_at":
                continue
            value = status.get(field)
            row[field] = "" if value is None else str(value)
        row["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_history(rows)
    except OSError as exc:
        print("Warning: status was fetched but history was not updated: %s" % exc)


def submit(data, judge_server=None, contest=None, ticket=None):
    judge_server = judge_server or JUDGE_SERVER
    contest = contest or CONTEST
    ticket = ticket or TICKET

    if not judge_server or not contest or not ticket:
        missing = [
            "judge_server" if not judge_server else "",
            "contest" if not contest else "",
            "ticket" if not ticket else "",
        ]
        missing = [item for item in missing if item]
        print("Required fields must be provided: %s" % ", ".join(missing))
        return None

    req_data = json.dumps({"data": data}).encode("utf-8")
    req = request.Request(
        judge_server,
        data=req_data,
        headers={
            "ticket": ticket,
            "contest": contest,
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req) as response:
            response_body = json.loads(response.read().decode("utf-8"))
            submission_id = response_body["submission_id"]
            remaining_attempts_today = response_body.get(
                "remaining_attempts_today", -1
            )
            _record_submission(submission_id)
            return submission_id, remaining_attempts_today
    except error.HTTPError as exc:
        message = exc.reason
        response_body = exc.read().decode("utf-8")
        if response_body:
            try:
                message = json.loads(response_body)["detail"]
            except (KeyError, json.JSONDecodeError):
                pass
        print("[Error %s] %s" % (exc.code, message))
    except error.URLError as exc:
        print(exc.reason)
        return None


def check_status(submission_id, judge_server=None, contest=None, ticket=None):
    judge_server = judge_server or JUDGE_SERVER
    contest = contest or CONTEST
    ticket = ticket or TICKET

    if not judge_server or not contest or not ticket or not submission_id:
        missing = [
            "judge_server" if not judge_server else "",
            "contest" if not contest else "",
            "ticket" if not ticket else "",
            "submission_id" if not submission_id else "",
        ]
        missing = [item for item in missing if item]
        print("Required fields must be provided: %s" % ", ".join(missing))
        return None

    req = request.Request(
        judge_server + "/status",
        headers={
            "ticket": ticket,
            "contest": contest,
            "submission": submission_id,
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req) as response:
            status = json.loads(response.read().decode("utf-8"))
        _record_status(status)
        return status
    except error.HTTPError as exc:
        message = exc.reason
        response_body = exc.read().decode("utf-8")
        if response_body:
            try:
                message = json.loads(response_body)["detail"]
            except (KeyError, json.JSONDecodeError):
                pass
        print("[Error %s] %s" % (exc.code, message))
    except error.URLError as exc:
        print(exc.reason)
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Submit to judge server")
    parser.add_argument(
        "result_path",
        nargs="?",
        default="result.jsonl",
        help="Path to the submission file, default is result.jsonl",
    )
    parser.add_argument("-s", "--server", help="Judge server URL")
    parser.add_argument("-c", "--contest", help="Contest ID")
    parser.add_argument("-k", "--ticket", help="Submission ticket")
    parser.add_argument(
        "-i",
        "--submission_id",
        help="Submission ID, specified if you want to check the submission status",
        default=None,
    )
    args = parser.parse_args()

    if args.submission_id:
        status = check_status(
            args.submission_id,
            judge_server=args.server,
            contest=args.contest,
            ticket=args.ticket,
        )
        if status:
            submission_id = status.get("submission_id")
            score = status.get("score")
            judge_time = status.get("judge_time")
            evaluation_error = status.get("error")
            if not judge_time:
                print("Submission %s is still in queue." % submission_id)
            elif evaluation_error:
                print("Submission %s failed: %s" % (submission_id, evaluation_error))
            else:
                print("Submission %s score: %s" % (submission_id, score))
                for label, key in (
                    ("Anomaly detection (AD)", "ad_score"),
                    ("Root-cause localization (RCA)", "rca_score"),
                    ("Major-category classification", "major_score"),
                    ("Minor-category classification", "minor_score"),
                ):
                    value = status.get(key)
                    if value is not None:
                        print("  %s: %s" % (label, value))
            raise SystemExit(0)
        print("Failed to check submission status.")
        raise SystemExit(1)

    try:
        with open(args.result_path, "r", encoding="utf-8") as file:
            data = [json.loads(line.strip()) for line in file if line.strip()]
    except Exception as exc:
        print(exc)
        raise SystemExit(1)

    return_data = submit(
        data,
        judge_server=args.server,
        contest=args.contest,
        ticket=args.ticket,
    )
    if return_data:
        submission_id, remaining_attempts_today = return_data
        print("Success! Your submission ID is %s." % submission_id)
        if remaining_attempts_today >= 0:
            print(
                "You have %d evaluation attempt(s) remaining today."
                % remaining_attempts_today
            )
        raise SystemExit(0)
    raise SystemExit(1)
