"""Restricted WorkBuddy JSON client."""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from credentials import ALLOWED_HOSTS, Session


ALLOWED_PATHS = {
    "/v2/billing/meter/checkin-activity-status",
    "/v2/billing/meter/checkin-status",
    "/v2/billing/meter/daily-checkin",
    "/v2/activity/growth/buddy/travel/status",
    "/v2/activity/growth/buddy/travel/config",
    "/v2/activity/growth/buddy/travel/depart",
    "/v2/activity/growth/buddy/travel/claim",
    "/activity/growth/buddy/travel/status",
    "/activity/growth/buddy/travel/config",
    "/activity/growth/buddy/travel/depart",
    "/activity/growth/buddy/travel/claim",
    "/v2/activity/growth/tasks",
    "/activity/growth/tasks",
    "/v2/activity/growth/tasks/accept",
    "/activity/growth/tasks/accept",
}


# Claiming takes the task code inside the path, so it is matched by pattern and the
# code itself is always validated against the codes the task list returned.
ALLOWED_PATH_PATTERNS = (
    r"^/v2/activity/growth/tasks/[A-Za-z0-9_-]{1,64}/claim$",
    r"^/activity/growth/tasks/[A-Za-z0-9_-]{1,64}/claim$",
)


def _path_allowed(path: str) -> bool:
    if path in ALLOWED_PATHS:
        return True
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED_PATH_PATTERNS)


@dataclass(frozen=True)
class Response:
    http_status: int
    body: Any
    network_error: bool = False


def dig(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value and value[key] is not None:
            return value[key]
        for envelope in ("data", "result", "resp", "response"):
            child = value.get(envelope)
            found = dig(child, key)
            if found is not None:
                return found
    return None


class WorkBuddyClient:
    def __init__(self, session: Session, timeout: float = 20.0):
        self.session = session
        self.timeout = timeout

    def _url(self, path: str) -> str:
        if not _path_allowed(path):
            raise ValueError("endpoint is not allowlisted")
        base = urllib.parse.urlsplit(self.session.api_base)
        if base.scheme != "https" or base.hostname not in ALLOWED_HOSTS:
            raise ValueError("host is not allowlisted")
        return self.session.api_base.rstrip("/") + path

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.session.token,
            "Content-Type": "application/json",
            "User-Agent": "WorkBuddy",
            "X-User-Id": self.session.uid,
        }
        if self.session.domain:
            headers["X-Domain"] = self.session.domain
        if self.session.enterprise_id:
            headers["X-Enterprise-Id"] = self.session.enterprise_id
            headers["X-Tenant-Id"] = self.session.enterprise_id
        return headers

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, retry_read: bool = False) -> Response:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        attempts = 2 if retry_read and method.upper() in {"GET", "HEAD"} else 1
        last = Response(-1, {"error": "network unavailable"}, network_error=True)
        for attempt in range(attempts):
            request = urllib.request.Request(self._url(path), data=body, headers=self._headers(), method=method.upper())
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
                    last = Response(response.status, _parse_json(response.read()))
            except urllib.error.HTTPError as error:
                last = Response(error.code, _parse_json(error.read()))
            except (urllib.error.URLError, TimeoutError, OSError):
                last = Response(-1, {"error": "network unavailable"}, network_error=True)
            if not last.network_error or attempt + 1 >= attempts:
                return last
            time.sleep(1)
        return last


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return {"error": "non-json response"}


def business_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    value = body.get("msg") or body.get("message") or dig(body, "msg") or ""
    if not isinstance(value, str):
        return ""
    value = value.replace("\r", " ").replace("\n", " ")
    return value[:200]
