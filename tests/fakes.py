"""A fake API for unit tests: httpx's MockTransport answering from a script, recording every request."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

import boxline

Reply = Union[httpx.Response, Exception, Callable[[httpx.Request], Union[httpx.Response, Exception]]]

SESSION_ID = "6c1f3c9e-1d2a-4f67-9b0e-1b2c3d4e5f60"


def session(sid: str = SESSION_ID, **extra: Any) -> Dict[str, Any]:
    return {"id": sid, "status": "RUNNING", "connectUrl": "ws://x", "liveUrl": "http://x", "terminalUrl": None, "workspacePath": "/workspace", **extra}


def reply(body: Any = None, status: int = 200, headers: Optional[Dict[str, str]] = None, content: Optional[bytes] = None) -> httpx.Response:
    if content is not None:
        return httpx.Response(status, content=content, headers=headers)
    if body is None:
        return httpx.Response(status, headers=headers)
    return httpx.Response(status, json=body, headers=headers)


def api_error(status: int, code: str, headers: Optional[Dict[str, str]] = None, request_id: str = "req_server1") -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": f"{code} happened", "requestId": request_id}},
        headers={"x-request-id": request_id, **(headers or {})},
    )


NO_WAIT = {"retry-after": "0"}


class Fake:
    """Answers from ``replies`` in order (the last one repeats); ``requests`` has every request sent."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if callable(r) and not isinstance(r, (httpx.Response, Exception)):
            r = r(request)
        if isinstance(r, Exception):
            raise r
        # A fresh response each time (the last reply repeats).
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    def sync(self, **kw: Any) -> boxline.Boxline:
        return boxline.Boxline(api_key="bxl_test", base_url="http://api.test/", http_client=httpx.Client(transport=httpx.MockTransport(self.handler)), **kw)

    def async_(self, **kw: Any) -> boxline.AsyncBoxline:
        return boxline.AsyncBoxline(api_key="bxl_test", base_url="http://api.test/", http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), **kw)

    def body(self, i: int) -> Any:
        content = self.requests[i].content
        return json.loads(content) if content else None
