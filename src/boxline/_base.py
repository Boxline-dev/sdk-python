"""What the sync and async clients share: configuration, headers, retry rules, query strings and stream parsing."""

from __future__ import annotations

import json
import os
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
from urllib.parse import quote

import httpx
from typing_extensions import TypedDict

from ._errors import BoxlineError, retry_after_seconds
from ._version import __version__

JSON = Dict[str, Any]
#: A proxy option: True (a residential US proxy), one proxy (a dict) or a list of rules by site; False for none.
Proxy = Union[bool, JSON, List[JSON]]

#: Where the SDK goes without base_url or BOXLINE_API_URL (the same default as the CLI).
DEFAULT_URL = "https://api.boxline.dev"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 2
#: A plain-English step can wait up to 4 minutes for a person to solve a CAPTCHA (session captcha "ask").
STEP_TIMEOUT = 420.0
#: A code wait (a pushed code or one from the credential's codeUrl) can take up to 900 s on the server.
CODE_WAIT_TIMEOUT = 960.0
#: The login action: 15 steps of 30 s plus the longest code wait, with a margin.
LOGIN_TIMEOUT = 1440.0
_WAITS_FOR_CODE = re.compile(r"%[A-Z0-9_]+\.(otp|link)%")


def actions_wait(items: "List[Any]") -> "Optional[float]":
    """How long an action list may take on the server: plain-English steps, code waits and the login action are slow."""
    wait = 0.0
    for a in items:
        if isinstance(a, str):
            wait = max(wait, STEP_TIMEOUT + (CODE_WAIT_TIMEOUT if _WAITS_FOR_CODE.search(a) else 0))
        elif a.get("action") == "login":
            wait = max(wait, LOGIN_TIMEOUT)
        elif a.get("action") == "step":
            wait = max(wait, STEP_TIMEOUT + (CODE_WAIT_TIMEOUT if _WAITS_FOR_CODE.search(str(a)) else 0))
        elif a.get("action") == "type" and a.get("credential") and a.get("field") == "otp":
            wait = max(wait, CODE_WAIT_TIMEOUT)
    return wait or None

#: The POSTs whose Idempotency-Key the API honours (docs/CONTRACT.md "Idempotency keys"); only these are retried.
#: ``:id`` stands for one path segment.
IDEMPOTENT_POSTS = frozenset(
    {
        "/v1/sessions",
        "/v1/sessions/bulk",
        "/v1/agent/runs",
        "/v1/agent/runs/:id/resume",
        "/v1/agent/runs/:id/messages",
        "/v1/crawls",
        "/v1/project/api-keys",
        "/v1/profiles",
        "/v1/extensions",
        "/v1/tasks",
        "/v1/tasks/:id/runs",
    }
)


def is_idempotent_post(route: str) -> bool:
    """Whether a POST to ``route`` (a path without its query) is one of IDEMPOTENT_POSTS."""
    if route in IDEMPOTENT_POSTS:
        return True
    parts = route.split("/")
    for pattern in IDEMPOTENT_POSTS:
        if "/:" not in pattern:
            continue
        want = pattern.split("/")
        if len(want) == len(parts) and all((p != "") if w.startswith(":") else (w == p) for w, p in zip(want, parts)):
            return True
    return False

#: Backoff: the first retry waits about INITIAL_DELAY, doubling per attempt up to MAX_DELAY, less up to 25% jitter.
INITIAL_DELAY = 0.5
MAX_DELAY = 8.0
#: A server asking to wait longer than this (Retry-After) is not retried: the error goes to the caller.
MAX_SERVER_DELAY = 60.0


class RequestOptions(TypedDict, total=False):
    """Options for one call; every SDK method takes them as ``options=``."""

    #: Time limit for this call in seconds (for streams: until the response starts).
    timeout: float
    #: Retries after a network error, 429 or 5xx (GETs, and POSTs with an Idempotency-Key).
    max_retries: int
    #: Idempotency-Key for the calls that create or start something. The SDK makes one per call by itself; pass
    #: your own to make a retry of your own safe too (e.g. a job id).
    idempotency_key: str
    #: Your own id for this request (1–64 of A–Z a–z 0–9 . _ : -), to find it in your logs: sent as
    #: X-Client-Request-Id, logged by the API next to its own id, and on errors as ``client_request_id``.
    client_request_id: str
    #: Extra headers for this call.
    headers: Dict[str, str]


@dataclass
class Config:
    api_key: Optional[str]
    base_url: str
    timeout: float
    max_retries: int
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: Optional[float],
        max_retries: Optional[int],
        headers: Optional[Mapping[str, str]],
    ) -> "Config":
        return cls(
            api_key=api_key or os.environ.get("BOXLINE_API_KEY"),
            base_url=(base_url or os.environ.get("BOXLINE_API_URL") or DEFAULT_URL).rstrip("/"),
            timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
            max_retries=DEFAULT_MAX_RETRIES if max_retries is None else max_retries,
            headers=dict(headers or {}),
        )


@dataclass
class Plan:
    """How one call is sent: its headers, whether it may be retried, how often and with what time limit."""

    headers: Dict[str, str]
    max_retries: int
    timeout: float


def plan_request(cfg: Config, method: str, path: str, options: Optional[RequestOptions], timeout: Optional[float], has_json: bool) -> Plan:
    opts: RequestOptions = options or {}
    route = path.split("?")[0]
    idempotent = method == "POST" and is_idempotent_post(route)
    key = opts.get("idempotency_key") or (str(uuid.uuid4()) if idempotent else None)
    headers = {
        "accept": "application/json",
        "user-agent": f"boxline-python/{__version__}",
        "boxline-sdk": f"python/{__version__}",
        **cfg.headers,
    }
    if cfg.api_key:
        headers["x-api-key"] = cfg.api_key
    if opts.get("client_request_id"):
        headers["x-client-request-id"] = opts["client_request_id"]
    if key:
        headers["idempotency-key"] = key
    if has_json:
        headers["content-type"] = "application/json"
    headers.update(opts.get("headers") or {})
    retryable = method in ("GET", "HEAD") or (idempotent and bool(key))
    max_retries = max(0, opts.get("max_retries", cfg.max_retries)) if retryable else 0
    limit = opts.get("timeout")
    if limit is None:
        limit = cfg.timeout if timeout is None else max(cfg.timeout, timeout)
    return Plan(headers=headers, max_retries=max_retries, timeout=limit)


def retry_delay(attempt: int, error: Optional[BoxlineError] = None, rand: Any = random.random) -> Optional[float]:
    """Seconds to wait before retry number ``attempt`` (0 = the first retry), or None when the server asked to wait
    longer than MAX_SERVER_DELAY. Retry-After wins; then RateLimit-Reset, but only when that window is used up
    (RateLimit-Remaining 0: a 429 ``concurrency_limit`` carries the request-rate window's headers too, and waiting for
    that window would not free a session); then exponential backoff with jitter."""
    server = _server_delay(error)
    if server is not None:
        return None if server > MAX_SERVER_DELAY else server
    base = min(INITIAL_DELAY * 2**attempt, MAX_DELAY)
    return base * (1 - rand() * 0.25)


def _server_delay(error: Optional[BoxlineError]) -> Optional[float]:
    headers = error.headers if error is not None else None
    if headers is None:
        return None
    after = retry_after_seconds(headers)
    if after is not None:
        return after
    reset = headers.get("ratelimit-reset")
    if reset is not None and headers.get("ratelimit-remaining") == "0":
        try:
            value = float(reset)
        except ValueError:
            return None
        if value >= 0:
            return value
    return None


def timeout_for(seconds: float, stream: bool) -> httpx.Timeout:
    # Streams (exec, scripts, event streams) can be quiet for minutes: the limit covers connecting and the answer's start.
    return httpx.Timeout(seconds, read=None) if stream else httpx.Timeout(seconds)


def clean(params: Mapping[str, Any]) -> Dict[str, Any]:
    """The entries that are not None."""
    return {k: v for k, v in params.items() if v is not None}


def query(params: Mapping[str, Any]) -> Dict[str, str]:
    """Query parameters from the values that are not None (lists comma-separated, booleans lower-case)."""
    out: Dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = ",".join(str(x) for x in v)
        elif isinstance(v, bool):
            out[k] = "true" if v else "false"
        else:
            out[k] = str(v)
    return out


def seg(value: str) -> str:
    """One path segment (an id), escaped."""
    return quote(str(value), safe="")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.title() for p in rest)


def json_line(line: str) -> Optional[JSON]:
    """One NDJSON line as a dict, or None for blank lines and anything that is not a JSON object."""
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class SSEParser:
    """Collects server-sent event lines; :meth:`feed` returns the event (a dict) a blank line completes."""

    def __init__(self) -> None:
        self._data: List[str] = []

    def feed(self, line: str) -> Optional[JSON]:
        line = line.rstrip("\r")
        if line == "":
            return self.flush()
        if line.startswith("data:"):
            self._data.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
        # ":" comments (pings), "event:", "id:" and "retry:" carry nothing the API uses.
        return None

    def flush(self) -> Optional[JSON]:
        if not self._data:
            return None
        text = "\n".join(self._data)
        self._data = []
        try:
            value = json.loads(text)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None


def as_list(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    return list(values) if values else None


class NotGiven:
    """Marks an argument that was not passed (for ones where None means something, e.g. removing a proxy)."""

    def __repr__(self) -> str:
        return "NOT_GIVEN"

    def __bool__(self) -> bool:
        return False


NOT_GIVEN = NotGiven()
