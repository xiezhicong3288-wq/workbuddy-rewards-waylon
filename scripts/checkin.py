"""Buddy gas-station check-in workflow."""

from __future__ import annotations

from typing import Any

from http_client import Response, WorkBuddyClient, business_message, dig


def _status_request(client: WorkBuddyClient) -> Response:
    response = client.request("POST", "/v2/billing/meter/checkin-activity-status", payload={})
    if response.http_status in (404, 405):
        response = client.request("POST", "/v2/billing/meter/checkin-status", payload={})
    return response


def _already(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    message = business_message(body).lower()
    return body.get("code") == 10001 or "已签到" in message or "already checked" in message


def _summary(body: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    mappings = {
        "credit": ("credit", "today_credit", "daily_credit"),
        "streak_days": ("streak_days", "continuous_days"),
        "total_credits": ("total_credits",),
    }
    for output, keys in mappings.items():
        for key in keys:
            value = dig(body, key)
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                result[output] = value
                break
    return result


def status(client: WorkBuddyClient) -> tuple[int, dict[str, Any]]:
    response = _status_request(client)
    if response.network_error:
        return 1, {"task": "checkin", "status": "failed", "reason": "network_unavailable"}
    if response.http_status == 401:
        return 2, {"task": "checkin", "status": "failed", "reason": "auth_rejected"}
    if response.http_status == 403:
        return 1, {"task": "checkin", "status": "failed", "reason": "forbidden"}
    if not 200 <= response.http_status < 300:
        return 1, {"task": "checkin", "status": "failed", "reason": "http_error", "http_status": response.http_status}
    checked = dig(response.body, "today_checked_in") in (True, 1)
    return 0, {"task": "checkin", "status": "already_checked" if checked else "available", **_summary(response.body)}


def run(client: WorkBuddyClient) -> tuple[int, dict[str, Any]]:
    code, current = status(client)
    if code or current["status"] == "already_checked":
        return code, current
    response = client.request("POST", "/v2/billing/meter/daily-checkin", payload={})
    if response.network_error:
        return 1, {"task": "checkin", "status": "failed", "reason": "ambiguous_network_result"}
    if response.http_status == 401:
        return 2, {"task": "checkin", "status": "failed", "reason": "auth_rejected"}
    if response.http_status == 403:
        return 1, {"task": "checkin", "status": "failed", "reason": "forbidden"}
    if (200 <= response.http_status < 300 and response.body is None) or _already(response.body):
        return 0, {"task": "checkin", "status": "already_checked", **_summary(response.body)}
    business_code = response.body.get("code") if isinstance(response.body, dict) else None
    if 200 <= response.http_status < 300 and business_code in (None, 0):
        return 0, {"task": "checkin", "status": "success", **_summary(response.body)}
    result: dict[str, Any] = {"task": "checkin", "status": "failed", "reason": "business_error"}
    if isinstance(business_code, (int, str)):
        result["business_code"] = business_code
    message = business_message(response.body)
    if message:
        result["message"] = message
    return 1, result
