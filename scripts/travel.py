"""Buddy travel one-transition state machine.

With ``--loop`` the same machine repeats, always re-reading state before each
transition, so an arrived Buddy is claimed and re-dispatched without waiting for
the next external trigger. Every iteration still performs at most one write, and
an ambiguous write response ends the run instead of being retried.
"""

from __future__ import annotations

import time
from typing import Any

from http_client import Response, WorkBuddyClient, business_message, dig


PREFIXES = ("/v2/activity/growth", "/activity/growth")
MAX_TRANSITIONS = 60
SLEEP_CAP_SECONDS = 900


def _request(client: WorkBuddyClient, method: str, suffix: str, payload: dict[str, Any] | None = None) -> Response:
    first = client.request(method, PREFIXES[0] + suffix, payload=payload, retry_read=method == "GET")
    if first.http_status in (404, 405):
        return client.request(method, PREFIXES[1] + suffix, payload=payload, retry_read=method == "GET")
    return first


def _failure(response: Response) -> tuple[int, dict[str, Any]] | None:
    if response.network_error:
        return 1, {"task": "travel", "status": "failed", "reason": "network_unavailable"}
    if response.http_status == 401:
        return 2, {"task": "travel", "status": "failed", "reason": "auth_rejected"}
    if response.http_status == 403:
        return 1, {"task": "travel", "status": "failed", "reason": "forbidden"}
    if not 200 <= response.http_status < 300:
        return 1, {"task": "travel", "status": "failed", "reason": "http_error", "http_status": response.http_status}
    if isinstance(response.body, dict) and response.body.get("code") not in (None, 0):
        result: dict[str, Any] = {"task": "travel", "status": "failed", "reason": "business_error", "business_code": response.body.get("code")}
        message = business_message(response.body)
        if message:
            result["message"] = message
        return 1, result
    return None


def _state(client: WorkBuddyClient) -> tuple[int, dict[str, Any], Any]:
    response = _request(client, "GET", "/buddy/travel/status")
    failed = _failure(response)
    if failed:
        return failed[0], failed[1], None
    state = dig(response.body, "state") or "unknown"
    out: dict[str, Any] = {"task": "travel", "status": state}
    location = dig(response.body, "location")
    if isinstance(location, dict) and isinstance(location.get("name"), str):
        out["location"] = location["name"][:100]
    for key in ("record_id", "arrive_at", "server_now", "reward_credit", "daily_limit_reached"):
        value = dig(response.body, key)
        if isinstance(value, (int, float, str, bool)):
            out[key] = value
    if state == "traveling" and out.get("arrive_at"):
        try:
            out["remaining_seconds"] = max(0, int(out["arrive_at"]) - int(out.get("server_now") or time.time()))
        except (TypeError, ValueError, OverflowError):
            pass
    return 0, out, response.body


def status(client: WorkBuddyClient) -> tuple[int, dict[str, Any]]:
    code, out, _ = _state(client)
    return code, out


def _ordered_locations(locations: Any) -> list[dict[str, Any]]:
    if not isinstance(locations, list):
        return []
    valid = [item for item in locations if isinstance(item, dict) and item.get("id") is not None]
    def rank(item: dict[str, Any]) -> tuple[Any, ...]:
        sort = item.get("sort")
        return (0, int(sort)) if isinstance(sort, (int, float)) and not isinstance(sort, bool) else (1, 0)
    return sorted(valid, key=rank)


def _pick_location(locations: Any, requested: str | None) -> dict[str, Any] | None:
    valid = _ordered_locations(locations)
    if requested:
        for item in valid:
            if str(item.get("id")) == requested or item.get("code") == requested:
                return item
        return None
    return valid[0] if valid else None


def run(client: WorkBuddyClient, location: str | None = None) -> tuple[int, dict[str, Any]]:
    """One observation followed by at most one state transition."""
    code, current, _ = _state(client)
    if code:
        return code, current
    return _act(client, current, location)


def _act(client: WorkBuddyClient, current: dict[str, Any], location: str | None) -> tuple[int, dict[str, Any]]:
    state = current.get("status")
    if state == "traveling":
        return 0, current
    if state == "arrived":
        payload = {"record_id": current["record_id"]} if current.get("record_id") is not None else {}
        response = _request(client, "POST", "/buddy/travel/claim", payload=payload)
        claim_message = business_message(response.body).lower()
        if response.http_status in (400, 409) and (
            "no unclaimed" in claim_message or "无可领取" in claim_message or "已领取" in claim_message
        ):
            return 0, {"task": "travel", "status": "already_claimed"}
        failed = _failure(response)
        if failed:
            if failed[1].get("reason") == "network_unavailable":
                failed[1]["reason"] = "ambiguous_network_result"
            return failed
        reward = dig(response.body, "reward_credit")
        if reward is None:
            reward = current.get("reward_credit")
        result: dict[str, Any] = {"task": "travel", "status": "claimed"}
        if isinstance(reward, (int, float, str)) and not isinstance(reward, bool):
            result["reward_credit"] = reward
        if current.get("record_id") is not None:
            result["record_id"] = current["record_id"]
        return 0, result
    if state == "idle" and current.get("daily_limit_reached") is True:
        current["status"] = "daily_limit_reached"
        return 0, current
    if state != "idle":
        return 1, {"task": "travel", "status": "failed", "reason": "unknown_state"}

    config = _request(client, "GET", "/buddy/travel/config")
    failed = _failure(config)
    if failed:
        return failed
    selected = _pick_location(dig(config.body, "locations"), location)
    if not selected:
        return 1, {"task": "travel", "status": "failed", "reason": "location_unavailable"}
    response = _request(client, "POST", "/buddy/travel/depart", payload={"location_id": selected["id"]})
    failed = _failure(response)
    if failed:
        if failed[1].get("reason") == "network_unavailable":
            failed[1]["reason"] = "ambiguous_network_result"
        return failed
    result = {"task": "travel", "status": "departed", "location_id": selected["id"]}
    if isinstance(selected.get("name"), str):
        result["location"] = selected["name"][:100]
    for key in ("record_id", "arrive_at"):
        value = dig(response.body, key)
        if isinstance(value, (int, float, str)):
            result[key] = value
    return 0, result


def _wait_seconds(current: dict[str, Any]) -> float:
    """Seconds to sleep until arrival, using the server clock and never unbounded."""
    try:
        arrive = int(current.get("arrive_at") or 0)
        now = int(current.get("server_now") or time.time())
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return min(SLEEP_CAP_SECONDS, max(0.0, arrive - now) + 2.0)


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != "task" and isinstance(v, (str, int, float, bool))}


def run_loop(client: WorkBuddyClient, location: str | None = None, max_hours: float = 12.0, poll_seconds: int = 60) -> tuple[int, dict[str, Any]]:
    """Chain claim and dispatch so the Buddy is never idle inside the time window.

    Each iteration re-reads state and performs at most one transition, so a failed
    write is never blindly retried. The loop stops once the daily limit is hit,
    once the window elapses, or on the first real failure.
    """
    deadline = time.time() + max(0.0, float(max_hours)) * 3600.0
    actions: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}
    while time.time() < deadline and len(actions) < MAX_TRANSITIONS:
        code, current, _ = _state(client)
        if code:
            if actions:
                return 0, {"task": "travel", "status": "loop_stopped", "transitions": len(actions), "actions": actions}
            return code, current
        final_state = _compact(current)
        if current.get("status") == "traveling":
            wait = _wait_seconds(current) or min(SLEEP_CAP_SECONDS, max(5, int(poll_seconds)))
            if time.time() + wait >= deadline:
                break
            time.sleep(wait)
            continue
        code, outcome = _act(client, current, location)
        final_state = _compact(outcome)
        if code:
            return code, {"task": "travel", "status": "loop_failed", "transitions": len(actions), "actions": actions, "state": final_state}
        if outcome.get("status") in {"claimed", "departed"}:
            actions.append(_compact(outcome))
            continue
        break
    result: dict[str, Any] = {"task": "travel", "status": "loop_done", "transitions": len(actions)}
    if actions:
        result["actions"] = actions
        credited = [item.get("reward_credit") for item in actions if isinstance(item.get("reward_credit"), (int, float))]
        if credited:
            result["claimed_credit"] = sum(credited)
    if final_state:
        result["state"] = final_state
    return 0, result
