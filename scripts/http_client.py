"""Restricted WorkBuddy JSON client.

Requests reuse one HTTPS connection per client: the API answers in a few hundred
milliseconds but a fresh TCP+TLS handshake costs about a second, and a single run
makes several calls. The reuse is deliberately conservative — a socket that has sat
idle too long is replaced rather than reused, and a failed *write* is never resent on
a new connection, because the first attempt may already have reached the server.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import time
import urllib.parse
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


# Servers close idle keep-alive sockets. Anything idle longer than this is replaced
# instead of reused, so a long --loop wait never writes into a dead socket.
IDLE_REUSE_SECONDS = 30.0

# One run gets a bounded network budget. Without it a bad network makes every call
# wait for its timeout, the scheduled run is killed before it finishes, and the whole
# day's outcome goes unrecorded. The budget must stay below the scheduler's own limit.
DEFAULT_BUDGET_SECONDS = 420.0
MAX_BUDGET_SECONDS = 540.0
BUDGET_ENV = "WORKBUDDY_BUDGET_SECONDS"


def resolve_budget_seconds() -> tuple[float, str | None]:
    """Read the per-run network budget from the environment, clamped to a safe range.

    Returns the seconds and, when the configured value was unusable, a short note so
    the run can say it did not honour the request. A bad value must never disable the
    guard, so it falls back instead of being trusted.
    """
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return DEFAULT_BUDGET_SECONDS, None
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_SECONDS, f"{BUDGET_ENV} 不是数字，已改用默认 {DEFAULT_BUDGET_SECONDS:g} 秒"
    if value <= 0:
        return DEFAULT_BUDGET_SECONDS, f"{BUDGET_ENV} 不是正数，已改用默认 {DEFAULT_BUDGET_SECONDS:g} 秒"
    if value > MAX_BUDGET_SECONDS:
        return MAX_BUDGET_SECONDS, f"{BUDGET_ENV} 超过上限，已夹到 {MAX_BUDGET_SECONDS:g} 秒"
    return value, None


def _path_allowed(path: str) -> bool:
    if path in ALLOWED_PATHS:
        return True
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED_PATH_PATTERNS)


@dataclass(frozen=True)
class Response:
    http_status: int
    body: Any
    network_error: bool = False
    budget_exhausted: bool = False


def budget_exhausted(response: Response) -> bool:
    """True when the run ran out of network time before this call was allowed."""
    return response.budget_exhausted


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
    def __init__(self, session: Session, timeout: float = 20.0, budget_seconds: float | None = None):
        self.session = session
        self.timeout = timeout
        if budget_seconds is None:
            self.budget_seconds, self.budget_warning = resolve_budget_seconds()
        else:
            self.budget_seconds = max(0.0, min(float(budget_seconds), MAX_BUDGET_SECONDS))
            self.budget_warning = None
        # The budget covers time spent waiting on the network, not time spent sleeping
        # between --loop polls: a long wait for a Buddy to arrive must not consume it.
        self._spent = 0.0
        self._ssl_context = ssl.create_default_context()
        self._connection: http.client.HTTPSConnection | None = None
        self._connection_host: str | None = None
        self._used_at = 0.0

    def budget_left(self) -> float:
        """Seconds of network time still available to this run."""
        return max(0.0, self.budget_seconds - self._spent)

    def _target(self) -> tuple[str, int]:
        base = urllib.parse.urlsplit(self.session.api_base)
        if base.scheme != "https" or base.hostname not in ALLOWED_HOSTS:
            raise ValueError("host is not allowlisted")
        return base.hostname, base.port or 443

    def _url(self, path: str) -> str:
        if not _path_allowed(path):
            raise ValueError("endpoint is not allowlisted")
        self._target()
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

    def _connection_for(self, force_fresh: bool = False) -> http.client.HTTPSConnection:
        host, port = self._target()
        stale = (
            force_fresh
            or self._connection is None
            or self._connection_host != host
            or time.monotonic() - self._used_at > IDLE_REUSE_SECONDS
        )
        if stale:
            self._discard()
            self._connection = http.client.HTTPSConnection(
                host, port, context=self._ssl_context, timeout=self.timeout
            )
            self._connection_host = host
            self._used_at = time.monotonic()
        return self._connection

    def _discard(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass
            self._connection = None
            self._connection_host = None

    def close(self) -> None:
        """Release the reused socket. Safe to call more than once."""
        self._discard()

    def _send(self, method: str, path: str, body: bytes | None) -> Response:
        verb = method.upper()
        headers = self._headers()
        for fresh in (False, True):
            connection = self._connection_for(force_fresh=fresh)
            try:
                connection.request(verb, path, body=body, headers=headers)
                response = connection.getresponse()
                raw = response.read()
            except (http.client.HTTPException, OSError):
                self._discard()
                if fresh:
                    break
                # Repeating a read on a new socket is harmless; repeating a write is
                # not, because the first attempt may already have been applied.
                if verb not in {"GET", "HEAD"}:
                    break
                continue
            self._used_at = time.monotonic()
            if response.will_close:
                self._discard()
            return Response(response.status, _parse_json(raw))
        return Response(-1, {"error": "network unavailable"}, network_error=True)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, retry_read: bool = False) -> Response:
        if not _path_allowed(path):
            raise ValueError("endpoint is not allowlisted")
        self._target()
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        verb = method.upper()
        read = verb in {"GET", "HEAD"}
        attempts = 2 if retry_read and read else 1
        last = Response(-1, {"error": "network unavailable"}, network_error=True)
        for attempt in range(attempts):
            if self.budget_left() <= 0:
                return Response(-2, {"error": "budget exhausted"}, budget_exhausted=True)
            started = time.monotonic()
            last = self._send(method, path, body)
            self._spent += time.monotonic() - started
            if last.budget_exhausted:
                return last
            # A read may be repeated, and a server-side 5xx is as transient as a
            # dropped socket. A write is never repeated, whatever the failure looks
            # like, because the first attempt may already have been applied.
            retryable = last.network_error or (read and 500 <= last.http_status < 600)
            if not retryable or attempt + 1 >= attempts:
                return last
            remaining = self.budget_left()
            if remaining <= 0:
                return last
            time.sleep(min(1.0, remaining))
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
