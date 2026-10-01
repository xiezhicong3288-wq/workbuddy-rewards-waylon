"""Growth-center task board: list tasks, accept them, claim finished rewards.

The server only counts usage that happens *after* a task is accepted, and only a
task in ``completed`` state can be claimed. Claiming anything else answers HTTP 400
``task not completed``, which this module reports as pending rather than a failure.

Read-only mode (`status`) never writes. `run()` performs one batched accept for the
tasks still unopened, then one claim per completed task.
"""

from __future__ import annotations

import re
from typing import Any

from http_client import Response, WorkBuddyClient, business_message, dig


PREFIXES = ("/v2/activity/growth", "/activity/growth")
LIST_SUFFIX = "/tasks"
ACCEPT_SUFFIX = "/tasks/accept"
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ACCEPT_BATCH = 50
MAX_TASKS = 300
HINT_LIMIT = 140

CLAIMED = "claimed"
COMPLETED = "completed"
NOT_ACCEPTED = "not_accepted"


def _request(client: WorkBuddyClient, method: str, suffix: str, payload: dict[str, Any] | None = None) -> Response:
    read = method.upper() == "GET"
    first = client.request(method, PREFIXES[0] + suffix, payload=payload, retry_read=read)
    if first.http_status in (404, 405):
        return client.request(method, PREFIXES[1] + suffix, payload=payload, retry_read=read)
    return first


def _failure(response: Response) -> tuple[int, dict[str, Any]] | None:
    if response.network_error:
        return 1, {"task": "tasks", "status": "failed", "reason": "network_unavailable"}
    if response.http_status == 401:
        return 2, {"task": "tasks", "status": "failed", "reason": "auth_rejected"}
    if response.http_status == 403:
        return 1, {"task": "tasks", "status": "failed", "reason": "forbidden"}
    if not 200 <= response.http_status < 300:
        return 1, {"task": "tasks", "status": "failed", "reason": "http_error", "http_status": response.http_status}
    if isinstance(response.body, dict) and response.body.get("code") not in (None, 0):
        result: dict[str, Any] = {"task": "tasks", "status": "failed", "reason": "business_error"}
        business_code = response.body.get("code")
        if isinstance(business_code, (int, str)):
            result["business_code"] = business_code
        message = business_message(response.body)
        if message:
            result["message"] = message
        return 1, result
    return None


def _text(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _normalize(raw: Any) -> dict[str, Any] | None:
    """Map one server task onto the compact record this skill reports."""
    if not isinstance(raw, dict):
        return None
    code = raw.get("task_code")
    if not isinstance(code, str) or CODE_PATTERN.fullmatch(code) is None:
        return None
    accepted_status = raw.get("accept_status")
    status = accepted_status if isinstance(accepted_status, str) else "unknown"
    progress = raw.get("progress") if isinstance(raw.get("progress"), dict) else {}
    current, target = _number(progress.get("current")), _number(progress.get("target"))
    task: dict[str, Any] = {
        "task_code": code,
        "title": _text(raw.get("title"), 60),
        "status": status,
        "reward_credit": _number(raw.get("reward_credit")) or 0,
    }
    if current is not None and target is not None:
        task["progress"] = f"{current}/{target}"
    if raw.get("locked") is True:
        task["locked"] = True
    hint = _text(raw.get("task_desc")) or _text(raw.get("description"), HINT_LIMIT)
    if hint:
        task["hint"] = hint[:HINT_LIMIT]
    return task


def fetch(client: WorkBuddyClient) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    response = _request(client, "GET", LIST_SUFFIX)
    failed = _failure(response)
    if failed:
        return failed[0], failed[1], []
    raw_tasks = dig(response.body, "tasks")
    if not isinstance(raw_tasks, list):
        return 1, {"task": "tasks", "status": "failed", "reason": "unexpected_payload"}, []
    tasks = [task for task in (_normalize(item) for item in raw_tasks[:MAX_TASKS]) if task]
    return 0, {"task": "tasks", "status": "ok"}, tasks


def _summarize(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    claimable = [task for task in tasks if task["status"] == COMPLETED]
    claimed = [task for task in tasks if task["status"] == CLAIMED]
    unopened = [task for task in tasks if task["status"] == NOT_ACCEPTED and not task.get("locked")]
    waiting = [
        task for task in tasks
        if task["status"] not in (CLAIMED, COMPLETED, NOT_ACCEPTED) or (task["status"] == NOT_ACCEPTED and task.get("locked"))
    ]
    return {
        "task": "tasks",
        "status": "ok",
        "total": len(tasks),
        "claimed_count": len(claimed),
        "claimable": [task["task_code"] for task in claimable],
        "claimable_credit": sum(task["reward_credit"] for task in claimable),
        "unopened": [task["task_code"] for task in unopened],
        "unopened_credit": sum(task["reward_credit"] for task in unopened),
        "waiting": [task["task_code"] for task in waiting],
        "waiting_credit": sum(task["reward_credit"] for task in waiting),
    }


def status(client: WorkBuddyClient) -> tuple[int, dict[str, Any]]:
    """Read-only view of the task board."""
    code, result, tasks = fetch(client)
    if code:
        return code, result
    return 0, {**_summarize(tasks), "tasks": tasks}


def _accept(client: WorkBuddyClient, codes: list[str]) -> tuple[list[str], dict[str, Any] | None]:
    accepted: list[str] = []
    for start in range(0, len(codes), ACCEPT_BATCH):
        batch = codes[start:start + ACCEPT_BATCH]
        response = _request(client, "POST", ACCEPT_SUFFIX, payload={"task_codes": batch})
        failed = _failure(response)
        if failed:
            return accepted, {**failed[1], "status": "failed"}
        results = dig(response.body, "results")
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and isinstance(item.get("task_code"), str):
                    accepted.append(item["task_code"])
        else:
            accepted.extend(batch)
    return accepted, None


def _claim_task(client: WorkBuddyClient, task: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    code = task["task_code"]
    response = _request(client, "POST", f"/tasks/{code}/claim", payload={})
    message = business_message(response.body).lower()
    if response.http_status in (400, 409) and (
        "already" in message or "claimed" in message or "已领取" in message or "已领取过" in message
    ):
        return CLAIMED, None
    if response.http_status == 400 and ("not completed" in message or "未完成" in message):
        return "pending", None
    failed = _failure(response)
    if failed:
        detail = failed[1]
        detail["task_code"] = code
        return "failed", detail
    credit = _number(dig(response.body, "credit"))
    return "claimed", {"task_code": code, "title": task["title"], "reward_credit": credit if credit is not None else task["reward_credit"]}


def run(client: WorkBuddyClient, accept: bool = True) -> tuple[int, dict[str, Any]]:
    """Accept every unopened task, then claim every reward the server has marked completed.

    Usage-based tasks still need the real action inside WorkBuddy; those are reported
    as ``pending`` with a hint so the assistant can tell the user what to do next.
    """
    code, result, tasks = fetch(client)
    if code:
        return code, result
    accepted: list[str] = []
    errors: list[dict[str, Any]] = []
    if accept:
        unopened = [task["task_code"] for task in tasks if task["status"] == NOT_ACCEPTED and not task.get("locked")]
        if unopened:
            accepted, failure = _accept(client, unopened)
            if failure:
                errors.append({k: v for k, v in failure.items() if k != "task"})
            if accepted:
                code, refreshed, tasks = fetch(client)
                if code:
                    return code, refreshed
    claimed: list[dict[str, Any]] = []
    refused: list[str] = []
    for task in tasks:
        if task["status"] != COMPLETED:
            continue
        outcome, detail = _claim_task(client, task)
        if outcome == "claimed" and detail:
            claimed.append(detail)
        elif outcome == "pending":
            refused.append(task["task_code"])
        elif outcome == "failed" and detail:
            errors.append(detail)
    # A task the server refuses to pay out is not claimable, however the list
    # described it a moment ago: report it as still waiting instead.
    def _relabel(task: dict[str, Any]) -> dict[str, Any]:
        if task["task_code"] in refused:
            return {**task, "status": "in_progress"}
        if any(item["task_code"] == task["task_code"] for item in claimed) or task["status"] == CLAIMED:
            return {**task, "status": CLAIMED}
        return task

    board = _summarize([_relabel(task) for task in tasks])
    if accepted:
        board["accepted"] = accepted
    if claimed:
        board["claimed"] = claimed
        board["claimed_credit"] = sum(item["reward_credit"] for item in claimed)
    if errors:
        board["errors"] = errors
    board["status"] = "claimed" if claimed else ("nothing_to_claim" if not errors else "failed")
    return (1 if errors else 0), board
