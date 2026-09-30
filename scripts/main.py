#!/usr/bin/env python3
"""CLI entry point for the WorkBuddy rewards skill."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import checkin
import schedule
import travel
from credentials import CredentialError, inspect_auth, load_session
from http_client import WorkBuddyClient


SAFE_LOG_KEYS = {
    "task", "status", "reason", "credit", "streak_days", "total_credits",
    "reward_credit", "record_id", "location", "location_id", "arrive_at",
    "remaining_seconds", "daily_limit_reached", "http_status", "business_code",
    "transitions", "claimed_credit",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WorkBuddy daily check-in and Buddy travel helper")
    parser.add_argument("command", choices=("doctor", "status", "checkin", "travel", "all", "schedule"))
    parser.add_argument("--location", help="Buddy travel location id or code")
    parser.add_argument("--from", dest="start", default="09:30", help="with schedule: when the machine turns on (HH:MM)")
    parser.add_argument("--to", dest="end", default="18:30", help="with schedule: when it turns off (HH:MM)")
    parser.add_argument("--no-log", action="store_true", help="do not append the sanitized result log")
    parser.add_argument("--loop", action="store_true", help="with travel/all: keep claiming and re-dispatching until the travel limit is reached")
    parser.add_argument("--max-hours", type=float, default=12.0, help="with --loop: stop after this many hours (default 12)")
    parser.add_argument("--poll-seconds", type=int, default=60, help="with --loop: fallback polling gap when no arrival time is known")
    return parser


def _safe_summary(value: Any) -> Any:
    if isinstance(value, list):
        return [_safe_summary(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_summary(item) for key, item in value.items() if key in SAFE_LOG_KEYS or key in {"checkin", "travel"}}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _append_log(result: dict[str, Any]) -> None:
    path = Path(__file__).resolve().parent.parent / "logs" / "result.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "result": _safe_summary(result)}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "schedule":
        raise SystemExit(schedule.main([f"--from={args.start}", f"--to={args.end}"]))
    if args.command == "doctor":
        return 0, {"task": "doctor", **inspect_auth()}
    session = load_session()
    client = WorkBuddyClient(session)
    if args.command == "status":
        ccode, cresult = checkin.status(client)
        tcode, tresult = travel.status(client)
        return max(ccode, tcode), {"checkin": cresult, "travel": tresult}
    if args.command == "checkin":
        return checkin.run(client)
    if args.command == "travel":
        if args.loop:
            return travel.run_loop(client, args.location, args.max_hours, args.poll_seconds)
        return travel.run(client, args.location)
    ccode, cresult = checkin.run(client)
    if args.loop:
        tcode, tresult = travel.run_loop(client, args.location, args.max_hours, args.poll_seconds)
    else:
        tcode, tresult = travel.run(client, args.location)
    return max(ccode, tcode), {"checkin": cresult, "travel": tresult}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, result = _run(args)
    except CredentialError as error:
        code, result = 2, {"task": args.command, "status": "failed", "reason": error.reason, "message": str(error)}
    except Exception as error:
        code, result = 1, {"task": args.command, "status": "failed", "reason": "unexpected_error", "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    if not args.no_log:
        _append_log(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
