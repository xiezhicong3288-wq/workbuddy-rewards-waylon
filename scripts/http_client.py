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
        self._ssl_context = ssl.create_default_context()
        self._connection: http.client.HTTPSConnection | None = None
        self._connection_host: str | None = None
        self._used_at = 0.0

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
        attempts = 2 if retry_read and method.upper() in {"GET", "HEAD"} else 1
        last = Response(-1, {"error": "network unavailable"}, network_error=True)
        for attempt in range(attempts):
            last = self._send(method, path, body)
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
