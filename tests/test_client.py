"""The Python SDK's reliability rules on a fake HTTP layer (no API): retries with backoff for GETs and idempotent
POSTs, Idempotency-Key handling, time limits, typed errors with request ids, cursor pages and streamed replies, for
both Boxline and AsyncBoxline."""

from __future__ import annotations

import asyncio
import email.utils
import json
import re
import time
from typing import Any, List

import httpx
import pytest

import boxline
from boxline import _async_client, _base, _client
from fakes import NO_WAIT, SESSION_ID, Fake, api_error, reply, session

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Backoff waits are recorded instead of slept."""
    delays: List[float] = []

    async def async_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(_client, "_sleep", delays.append)
    monkeypatch.setattr(_async_client, "_sleep", async_sleep)
    return delays


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------- headers


def test_headers_api_key_version_and_json_only_with_a_body() -> None:
    f = Fake(reply({"ok": True}))
    bx = f.sync()
    assert bx.base_url == "http://api.test"
    bx.health()
    bx.profiles.update("prof_1", name="n")
    get, patch = f.requests
    assert get.headers["x-api-key"] == "bxl_test"
    assert get.headers["boxline-sdk"] == f"python/{boxline.__version__}" == "python/3.1.0"
    assert "content-type" not in get.headers
    assert get.url.path == "/healthz"
    assert patch.headers["content-type"] == "application/json"
    assert f.body(1) == {"name": "n"}
    assert "idempotency-key" not in patch.headers


def test_client_request_id_and_extra_headers() -> None:
    f = Fake(reply({}))
    f.sync(headers={"x-team": "a"}).me(options={"client_request_id": "trace-42", "headers": {"x-extra": "1"}})
    h = f.requests[0].headers
    assert (h["x-client-request-id"], h["x-team"], h["x-extra"]) == ("trace-42", "a", "1")
    assert "x-request-id" not in h


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOXLINE_API_KEY", "bxl_env")
    monkeypatch.setenv("BOXLINE_API_URL", "http://env.test")
    f = Fake(reply({"ok": True}))
    bx = boxline.Boxline(http_client=httpx.Client(transport=httpx.MockTransport(f.handler)))
    bx.health()
    assert str(f.requests[0].url) == "http://env.test/healthz"
    assert f.requests[0].headers["x-api-key"] == "bxl_env"


# ---------------------------------------------------------------- retries


def test_retries_a_get_after_503_and_network_errors(no_sleep: List[float]) -> None:
    f = Fake(api_error(503, "no_capacity"), httpx.ConnectError("refused"), reply({"ok": True}))
    assert f.sync().health() == {"ok": True}
    assert len(f.requests) == 3
    # Backoff: about 0.5 s, then about 1 s (less up to 25% jitter).
    assert 0.375 <= no_sleep[0] <= 0.5 and 0.75 <= no_sleep[1] <= 1.0


def test_gives_up_after_max_retries() -> None:
    f = Fake(api_error(500, "internal", NO_WAIT))
    with pytest.raises(boxline.BoxlineError) as e:
        f.sync().me()
    assert e.value.status == 500 and len(f.requests) == 3
    g = Fake(api_error(500, "internal", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        g.sync(max_retries=5).me(options={"max_retries": 0})
    assert len(g.requests) == 1
    h = Fake(api_error(500, "internal", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        h.sync().with_options(max_retries=1).me()
    assert len(h.requests) == 2


def test_never_retries_a_post_without_idempotency_key() -> None:
    f = Fake(api_error(503, "machine_unreachable", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        f.sync().sessions.exec(SESSION_ID, "rm -rf build")
    g = Fake(httpx.ConnectError("reset"))
    with pytest.raises(boxline.BoxlineConnectionError):
        g.sync().fetch("https://example.com")
    assert len(f.requests) == 1 and len(g.requests) == 1


@pytest.mark.parametrize(
    "status,code",
    [(400, "invalid_request"), (404, "not_found"), (402, "feature_not_in_plan"), (502, "page_unreachable"), (504, "page_timeout"), (503, "provider_unavailable"), (501, "not_supported")],
)
def test_does_not_retry_client_page_or_configuration_errors(status: int, code: str) -> None:
    f = Fake(api_error(status, code, NO_WAIT))
    with pytest.raises(boxline.BoxlineError) as e:
        f.sync().sessions.get(SESSION_ID)
    assert e.value.code == code and len(f.requests) == 1


def test_429_honours_retry_after_and_passes_long_waits_to_the_caller(no_sleep: List[float]) -> None:
    f = Fake(api_error(429, "rate_limited", {"retry-after": "3", "ratelimit-reset": "3"}), reply({"ok": True}))
    f.sync().health()
    assert no_sleep == [3.0]
    g = Fake(api_error(429, "rate_limited", {"retry-after": "3600"}))
    with pytest.raises(boxline.RateLimitError) as e:
        g.sync().health()
    assert e.value.retry_after == 3600 and len(g.requests) == 1


# ---------------------------------------------------------------- idempotency keys


def test_idempotency_key_is_made_once_per_call_and_reused_on_retries() -> None:
    f = Fake(api_error(503, "no_capacity", NO_WAIT), api_error(409, "idempotency_in_progress", NO_WAIT), reply(session(), 201))
    s = f.sync().sessions.create(timeout=60)
    assert s.id == SESSION_ID and len(f.requests) == 3
    keys = [r.headers["idempotency-key"] for r in f.requests]
    assert UUID4.match(keys[0]) and len(set(keys)) == 1
    assert all(f.body(i) == {"browser": True, "shell": False, "timeout": 60} for i in range(3))


def test_new_key_per_call_and_the_callers_own_key() -> None:
    f = Fake(reply({"id": "run_1", "status": "running", "sessionId": "s", "provider": "anthropic", "model": "m"}, 201))
    bx = f.sync()
    bx.agent.run("a")
    bx.agent.run("a")
    bx.agent.run("a", options={"idempotency_key": "job-42"})
    keys = [r.headers["idempotency-key"] for r in f.requests]
    assert keys[0] != keys[1] and keys[2] == "job-42"


def test_keys_on_exactly_the_create_routes() -> None:
    f = Fake(reply({**session(), "results": [], "data": [], "next": None}, 201))
    bx = f.sync()
    bx.sessions.create()
    bx.sessions.bulk("stop", [SESSION_ID])
    bx.agent.run("t")
    bx.agent.resume("run_1", max_steps=None)
    bx.agent.send_message("run_1", "hi")
    bx.crawl.start("https://example.com")
    bx.api_keys.create("k")
    bx.profiles.create("c")
    bx.extensions.upload(b"\x01")
    bx.tasks.create("n", "i")
    bx.tasks.run("task_1")
    bx.sessions.update(SESSION_ID, timeout=600)
    bx.sessions.actions(SESSION_ID, {"action": "back"})
    bx.extensions.delete("ext_1")
    bx.tasks.update("task_1", name="m")
    bx.tasks.delete("task_1")
    with_key = [r.url.path for r in f.requests if "idempotency-key" in r.headers]
    assert with_key == [
        "/v1/sessions",
        "/v1/sessions/bulk",
        "/v1/agent/runs",
        "/v1/agent/runs/run_1/resume",
        "/v1/agent/runs/run_1/messages",
        "/v1/crawls",
        "/v1/project/api-keys",
        "/v1/profiles",
        "/v1/extensions",
        "/v1/tasks",
        "/v1/tasks/task_1/runs",
    ]

    def pattern(p: str) -> str:
        p = re.sub(r"^/v1/tasks/[^/]+/runs$", "/v1/tasks/:id/runs", p)
        return re.sub(r"^/v1/agent/runs/[^/]+/(resume|messages)$", r"/v1/agent/runs/:id/\1", p)

    assert {pattern(p) for p in with_key} == set(boxline.IDEMPOTENT_POSTS)


def test_optional_limits_send_null_only_when_asked() -> None:
    f = Fake(reply({"id": "run_1", "status": "running"}, 201))
    bx = f.sync()
    bx.agent.run("t")  # not passed: the API's default (30 steps)
    bx.agent.run("t", max_steps=None, max_cost_usd=0.5, session={"timeout": 600, "idleTimeout": 120}, max_consecutive_errors=3)
    bx.agent.resume("run_1")
    bx.agent.resume("run_1", max_steps=None, max_cost_usd=None, variables={"pw": "x"})
    bodies = [json.loads(r.content) for r in f.requests]
    assert "maxSteps" not in bodies[0]
    assert bodies[1] == {"task": "t", "session": {"timeout": 600, "idleTimeout": 120}, "maxSteps": None, "maxCostUsd": 0.5, "maxConsecutiveErrors": 3}
    assert bodies[2] == {}
    assert bodies[3] == {"maxSteps": None, "maxCostUsd": None, "variables": {"pw": "x"}}


def test_id_in_an_idempotent_route_is_one_non_empty_segment() -> None:
    assert boxline.is_idempotent_post("/v1/tasks/task_1/runs")
    assert boxline.is_idempotent_post("/v1/tasks/task%2F1/runs")
    for route in ("/v1/tasks//runs", "/v1/tasks/a/b/runs", "/v1/tasks/task_1", "/v1/tasks/task_1/runs/x", "/v1/sessions/s1/stop", "/v1/agent/runs/run_1/pause"):
        assert not boxline.is_idempotent_post(route), route


def test_mismatch_is_typed_and_not_retried() -> None:
    f = Fake(api_error(422, "idempotency_mismatch"))
    with pytest.raises(boxline.IdempotencyMismatchError) as e:
        f.sync().api_keys.create("x", options={"idempotency_key": "k1"})
    assert not e.value.retryable and len(f.requests) == 1


# ---------------------------------------------------------------- backoff


def test_backoff_doubles_to_eight_seconds_with_jitter() -> None:
    assert _base.retry_delay(0, rand=lambda: 0) == 0.5
    assert _base.retry_delay(1, rand=lambda: 0) == 1.0
    assert _base.retry_delay(3, rand=lambda: 0) == 4.0
    assert _base.retry_delay(10, rand=lambda: 0) == 8.0
    for _ in range(50):
        assert 1.5 <= _base.retry_delay(2) <= 2.0  # type: ignore[operator]


def test_backoff_follows_retry_after_then_ratelimit_reset() -> None:
    def err(status: int, **headers: str) -> boxline.BoxlineError:
        return boxline.make_error(status, "x", "x", headers=httpx.Headers({k.replace("_", "-"): v for k, v in headers.items()}))

    assert _base.retry_delay(0, err(429, retry_after="7")) == 7.0
    date = email.utils.formatdate(time.time() + 10, usegmt=True)
    assert 8 < _base.retry_delay(0, err(503, retry_after=date)) <= 10  # type: ignore[operator]
    assert _base.retry_delay(0, err(429, ratelimit_reset="12", ratelimit_remaining="0")) == 12.0
    assert _base.retry_delay(0, err(503, ratelimit_reset="12", ratelimit_remaining="5"), rand=lambda: 0) == 0.5
    # A 429 concurrency_limit carries the request-rate window's headers too; that window is not what frees a session.
    busy = boxline.make_error(429, "concurrency_limit", "x", headers=httpx.Headers({"ratelimit-reset": "50", "ratelimit-remaining": "80"}))
    assert _base.retry_delay(0, busy, rand=lambda: 0) == 0.5
    assert _base.retry_delay(0, err(429, retry_after="61")) is None


# ---------------------------------------------------------------- time limits


def test_timeouts_raise_boxline_timeout_error_and_retry_gets() -> None:
    f = Fake(httpx.ReadTimeout("slow"))
    with pytest.raises(boxline.BoxlineTimeoutError) as e:
        f.sync().me()
    assert e.value.code == "timeout" and e.value.retryable and len(f.requests) == 3


def test_time_limits_per_request_and_for_long_calls() -> None:
    f = Fake(reply({"exitCode": 0}), reply({"path": "a.csv", "size": 1}))
    bx = f.sync(timeout=5)
    bx.sessions.exec(SESSION_ID, "sleep 100", timeout_ms=600_000)
    bx.sessions.files.wait_for(SESSION_ID, "*.csv", timeout_ms=90_000, options={"timeout": 7})
    assert f.requests[0].extensions["timeout"]["read"] == 1230.0  # the command, 30 s, and up to 10 min of setup first
    assert f.requests[1].extensions["timeout"]["read"] == 7
    g = Fake(reply({"results": []}))
    g.sync(timeout=5).sessions.actions(SESSION_ID, {"action": "step", "instruction": "click"})
    assert g.requests[0].extensions["timeout"]["read"] == 420.0


# ---------------------------------------------------------------- errors


def test_errors_carry_both_request_ids() -> None:
    f = Fake(api_error(404, "not_found", {"x-client-request-id": "mine-1"}, "req_abc"))
    with pytest.raises(boxline.NotFoundError) as e:
        f.sync().sessions.get("nope", options={"client_request_id": "mine-1"})
    err = e.value
    assert isinstance(err, boxline.BoxlineError)
    assert (err.status, err.code, err.message, err.request_id, err.client_request_id) == (404, "not_found", "not_found happened", "req_abc", "mine-1")
    assert "req_abc" in repr(err)


def test_errors_carry_details_of_which_limit_refused() -> None:
    body = {"error": {"code": "out_of_credit", "message": "no credit left", "requestId": "req_x", "details": {"limit": "credit"}}}
    f = Fake(httpx.Response(402, json=body))
    with pytest.raises(boxline.BoxlineError) as e:
        f.sync().sessions.create()
    assert (e.value.status, e.value.code, e.value.details) == (402, "out_of_credit", {"limit": "credit"})
    f = Fake(api_error(404, "not_found"))
    with pytest.raises(boxline.BoxlineError) as e:
        f.sync().sessions.get("nope")
    assert e.value.details is None


def test_request_id_from_the_header_and_non_json_bodies() -> None:
    f = Fake(reply(content=b"<html>Bad</html>", status=400, headers={"x-request-id": "req_hdr"}))
    with pytest.raises(boxline.BoxlineError) as e:
        f.sync().sessions.get("s1")
    assert (e.value.status, e.value.code, e.value.request_id) == (400, "http_error", "req_hdr")
    assert "GET /v1/sessions/s1 failed with 400" in str(e.value)


@pytest.mark.parametrize(
    "status,code,cls",
    [
        (429, "rate_limited", boxline.RateLimitError),
        (429, "concurrency_limit", boxline.RateLimitError),
        (402, "feature_not_in_plan", boxline.FeatureNotInPlanError),
        (403, "project_suspended", boxline.ProjectSuspendedError),
        (422, "idempotency_mismatch", boxline.IdempotencyMismatchError),
        (409, "idempotency_in_progress", boxline.IdempotencyInProgressError),
        (400, "invalid_cursor", boxline.InvalidCursorError),
        (409, "captcha_timeout", boxline.CaptchaTimeoutError),
        (502, "page_unreachable", boxline.PageUnreachableError),
        (504, "page_timeout", boxline.PageTimeoutError),
        (409, "nothing_saved", boxline.NothingSavedError),
        (409, "session_not_running", boxline.SessionNotRunningError),
        (401, "unauthorized", boxline.AuthenticationError),
    ],
)
def test_codes_map_to_classes(status: int, code: str, cls: type) -> None:
    e = boxline.make_error(status, code, "m")
    assert isinstance(e, cls) and e.code == code
    assert getattr(boxline.ErrorCode, code.upper()) == code


def test_retryable() -> None:
    assert boxline.make_error(503, "no_capacity", "m").retryable
    assert boxline.make_error(409, "idempotency_in_progress", "m").retryable
    assert not boxline.make_error(502, "page_unreachable", "m").retryable
    assert not boxline.make_error(504, "page_timeout", "m").retryable
    assert not boxline.make_error(400, "invalid_request", "m").retryable


def test_failed_steps_raise_captcha_timeout_or_action_errors() -> None:
    f = Fake(
        reply(session()),
        reply({"results": [{"ok": False, "action": "step", "error": "still waiting", "code": "captcha_timeout", "ms": 1}]}),
        reply({"results": [{"ok": False, "action": "click", "error": "no element", "ms": 1}]}),
    )
    s = f.sync().sessions.get(SESSION_ID)
    with pytest.raises(boxline.CaptchaTimeoutError) as e1:
        s.step("click Sign in")
    assert e1.value.status == 409
    with pytest.raises(boxline.BoxlineError) as e2:
        s.click("#nope")
    assert (e2.value.status, e2.value.code, e2.value.message) == (400, "action_failed", "no element")


# ---------------------------------------------------------------- pagination


def pages(request: httpx.Request) -> httpx.Response:
    after = request.url.params.get("after")
    page = 1 if after is None else int(after[1:])
    return reply({"data": [session(f"{page}-{i}") for i in (1, 2)], "total": 6, "limit": 2, "next": f"c{page + 1}" if page < 3 else None})


def test_list_returns_the_first_page_and_iterates_every_item() -> None:
    f = Fake(pages)
    bx = f.sync()
    first = bx.sessions.list(limit=2, status=["RUNNING", "STOPPED"])
    assert [s.id for s in first.data] == ["1-1", "1-2"]
    assert (first.total, first["total"], first.next, first.has_next_page(), len(first)) == (6, 6, "c2", True, 2)
    assert f.requests[0].url.query == b"status=RUNNING%2CSTOPPED&limit=2"
    before = len(f.requests)
    assert [s.id for s in bx.sessions.list(limit=2)] == ["1-1", "1-2", "2-1", "2-2", "3-1", "3-2"]
    assert [r.url.params.get("after") for r in f.requests[before:]] == [None, "c2", "c3"]
    p2 = first.get_next_page()
    assert [s.id for s in p2.data] == ["2-1", "2-2"]
    assert [p.next for p in first.iter_pages()] == ["c2", "c3", None]
    last = p2.get_next_page()
    with pytest.raises(RuntimeError):
        last.get_next_page()


def test_list_from_a_cursor_and_invalid_cursor() -> None:
    f = Fake(pages)
    assert [c["id"] for c in f.sync().profiles.list(after="c3")] == ["3-1", "3-2"]
    g = Fake(api_error(400, "invalid_cursor"))
    with pytest.raises(boxline.InvalidCursorError):
        g.sync().crawl.list(after="nope")


def test_events_follow_next_until_caught_up() -> None:
    def events(request: httpx.Request) -> httpx.Response:
        after = int(request.url.params.get("after") or 0)
        data = [{"seq": s, "at": "t", "type": "console"} for s in (after + 1, after + 2)] if after < 4 else []
        return reply({"data": data, "nextAfter": data[-1]["seq"] if data else after, "next": str(after + 2) if data and after + 2 < 4 else None})

    f = Fake(events)
    bx = f.sync()
    page = bx.sessions.events(SESSION_ID, types=["console", "error"])
    assert page.next_after == 2 and page["data"][0]["seq"] == 1
    assert f.requests[0].url.params["types"] == "console,error"
    assert [e["seq"] for e in bx.sessions.events(SESSION_ID)] == [1, 2, 3, 4]


def test_crawl_pages_and_wait() -> None:
    def crawl(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("after") or 0)
        return reply({"id": "crawl_1", "status": "completed", "data": [{"index": start}, {"index": start + 1}], "next": str(start + 2) if start < 2 else None})

    f = Fake(crawl)
    assert [p["index"] for p in f.sync().crawl.pages("crawl_1", limit=2)] == [0, 1, 2, 3]
    job = f.sync().crawl.wait("crawl_1", poll=0)
    assert [p["index"] for p in job["data"]][:2] == [0, 1] and job["next"] is None


# ---------------------------------------------------------------- streams


def test_exec_stream_and_scripts() -> None:
    nd = b'{"type":"stdout","data":"a\\n"}\n{"type":"stderr","data":"b"}\n{"type":"exit","exitCode":3,"timedOut":false,"durationMs":5,"truncated":true}\n'
    f = Fake(reply(content=nd, headers={"content-type": "application/x-ndjson"}))
    bx = f.sync()
    with bx.sessions.exec_stream(SESSION_ID, "x") as proc:
        out = [f"{s}:{t}" for s, t in proc]
    assert out == ["stdout:a\n", "stderr:b"]
    assert proc.result is not None and proc.result["exitCode"] == 3 and proc.result["truncated"] is True
    assert proc.wait()["exitCode"] == 3  # already consumed: returns the result
    assert f.body(0)["stream"] is True
    script = bx.sessions.run_script(SESSION_ID, "console.log(1)", ai={"model": "claude-haiku-4-5"})
    assert script.wait()["exitCode"] == 3
    assert f.body(1)["ai"] == {"model": "claude-haiku-4-5"}


def test_exec_stream_skips_keepalives_and_raises_an_error_line() -> None:
    nd = b'{"type":"waiting","for":"setup"}\n{"type":"ping"}\n{"type":"stdout","data":"a"}\n{"type":"error","status":502,"code":"machine_unreachable","message":"could not reach the session machine","requestId":"req_1"}\n'
    f = Fake(reply(content=nd, headers={"content-type": "application/x-ndjson"}))
    out = []
    with pytest.raises(boxline.BoxlineError) as e:
        with f.sync().sessions.exec_stream(SESSION_ID, "x") as proc:
            for _, t in proc:
                out.append(t)
    assert out == ["a"]
    assert (e.value.status, e.value.code, e.value.request_id) == (502, "machine_unreachable", "req_1")


def test_agent_stream_stops_at_done() -> None:
    sse = b': ping\n\ndata: {"type":"text","at":"t","text":"hi"}\n\ndata: {"type":"done","status":"completed","result":"42","error":null}\n\ndata: {"type":"text"}\n\n'
    f = Fake(reply(content=sse, headers={"content-type": "text/event-stream"}))
    assert [e["type"] for e in f.sync().agent.stream("run_1")] == ["text", "done"]


def test_session_event_stream() -> None:
    f = Fake(reply(content=b'data: {"seq":1,"type":"console"}\n\ndata: {"seq":2,"type":"navigation"}\n\n', headers={"content-type": "text/event-stream"}))
    events = f.sync().sessions.stream_events(SESSION_ID, after=0)
    assert next(events)["seq"] == 1
    events.close()
    assert f.requests[0].url.query == b"after=0"


def test_events_of_one_agent_run() -> None:
    f = Fake(reply({"data": [{"seq": 1, "at": "t", "type": "exec", "data": {"by": "agent", "runId": "run_1", "step": 2, "files": ["title.txt"]}}], "nextAfter": 1, "next": None}))
    bx = f.sync()
    page = bx.sessions.events(SESSION_ID, types=["exec"], run_id="run_1")
    assert page["data"][0]["data"]["files"] == ["title.txt"]
    assert f.requests[0].url.params["runId"] == "run_1" and f.requests[0].url.params["types"] == "exec"
    g = Fake(reply(content=b'data: {"seq":1,"type":"files"}\n\n', headers={"content-type": "text/event-stream"}))
    events = g.sync().sessions.stream_events(SESSION_ID, run_id="run_1")
    assert next(events)["seq"] == 1
    events.close()
    assert g.requests[0].url.params["runId"] == "run_1"


# ---------------------------------------------------------------- session objects


def test_session_objects_delegate_and_stay_fresh() -> None:
    f = Fake(
        reply(session()),
        reply(session(status="STOPPED")),
        reply(session(status="RUNNING")),
        reply(session(status="RUNNING", timeout=600, connectUrl="ws://connect/2", liveUrl="http://live/2")),
        reply(session(status="STOPPED")),
    )
    bx = f.sync()
    with bx.sessions.get(SESSION_ID) as s:
        s.stop()
        assert s.status == "STOPPED"
        s.resume()
        assert s.status == "RUNNING"
        # One call changes a running session: a length, a new proxy IP, new signed URLs; the answer refreshes the handle.
        s.update(timeout=600, rotate_proxy=True, rotate_urls=True)
        assert s.data["timeout"] == 600 and s.connect_url == "ws://connect/2" and s.live_url == "http://live/2"
        assert s.workspace_path == "/workspace" and s.terminal_url is None
    assert s.status == "STOPPED"  # leaving the block stopped it
    assert [f"{r.method} {r.url.path}" for r in f.requests] == [
        f"GET /v1/sessions/{SESSION_ID}",
        f"POST /v1/sessions/{SESSION_ID}/stop",
        f"POST /v1/sessions/{SESSION_ID}/resume",
        f"PATCH /v1/sessions/{SESSION_ID}",
        f"POST /v1/sessions/{SESSION_ID}/stop",
    ]
    assert f.body(3) == {"timeout": 600, "rotateProxy": True, "rotateUrls": True}


def test_select_elements_and_proxy_removal() -> None:
    f = Fake(
        reply(session()),
        reply({"results": [{"ok": True, "action": "select", "value": None, "ms": 1}]}),
        reply({"results": [{"ok": True, "action": "elements", "value": {"url": "u", "title": "t", "text": "x", "elements": "[1] button", "count": 1}, "ms": 1}]}),
        reply(session()),
    )
    s = f.sync().sessions.get(SESSION_ID)
    s.select("#country", "Germany")
    assert s.elements()["count"] == 1
    s.update(proxy=None)
    assert f.body(1) == {"actions": [{"action": "select", "selector": "#country", "option": "Germany"}]}
    assert f.body(2) == {"actions": [{"action": "elements"}]}
    assert f.body(3) == {"proxy": None}


def test_ids_are_escaped_in_paths() -> None:
    f = Fake(reply({}))
    f.sync().agent.get("run/../x")
    assert f.requests[0].url.raw_path == b"/v1/agent/runs/run%2F..%2Fx"


# ---------------------------------------------------------------- the async client


def test_async_retries_keys_and_errors(no_sleep: List[float]) -> None:
    async def main() -> None:
        f = Fake(api_error(503, "no_capacity"), httpx.ConnectError("refused"), reply(session(), 201))
        async with f.async_() as bx:
            s = await bx.sessions.create()
            assert isinstance(s, boxline.AsyncSession) and s.id == SESSION_ID
            keys = {r.headers["idempotency-key"] for r in f.requests}
            assert len(f.requests) == 3 and len(keys) == 1
            assert f.requests[0].headers["boxline-sdk"] == "python/3.1.0"
        g = Fake(api_error(404, "not_found", {"x-client-request-id": "c1"}, "req_1"))
        with pytest.raises(boxline.NotFoundError) as e:
            await g.async_().sessions.get("x", options={"client_request_id": "c1"})
        assert (e.value.request_id, e.value.client_request_id) == ("req_1", "c1")

    run(main())
    assert len(no_sleep) == 2


def test_async_pagination_await_and_async_for() -> None:
    async def main() -> None:
        f = Fake(pages)
        bx = f.async_()
        pager = bx.sessions.list(limit=2)
        assert f.requests == []  # nothing is requested until it is awaited or iterated
        first = await pager
        assert [s.id for s in first.data] == ["1-1", "1-2"] and first.total == 6
        assert [s.id async for s in bx.sessions.list(limit=2)] == ["1-1", "1-2", "2-1", "2-2", "3-1", "3-2"]
        p2 = await first.get_next_page()
        assert p2.next == "c3"
        assert [p.next async for p in first.iter_pages()] == ["c2", "c3", None]

    run(main())


def test_async_streams_and_session_objects() -> None:
    async def main() -> None:
        sse = b'data: {"type":"text"}\n\ndata: {"type":"done","status":"completed"}\n\n'
        f = Fake(reply(content=sse, headers={"content-type": "text/event-stream"}))
        assert [e["type"] async for e in f.async_().agent.stream("run_1")] == ["text", "done"]
        nd = b'{"type":"stdout","data":"hi"}\n{"type":"exit","exitCode":0}\n'
        g = Fake(reply(session()), reply(content=nd), reply(session(status="STOPPED")))
        bx = g.async_()
        async with await bx.sessions.get(SESSION_ID) as s:
            proc = await s.exec_stream("echo hi")
            async with proc:
                assert [t async for _, t in proc] == ["hi"]
            assert proc.result is not None and proc.result["exitCode"] == 0
        assert s.status == "STOPPED"
        await bx.aclose()

    run(main())


def test_sync_and_async_have_the_same_surface() -> None:
    pairs = [
        (boxline.Boxline, boxline.AsyncBoxline),
        (boxline.Sessions, boxline.AsyncSessions),
        (boxline.SessionFiles, boxline.AsyncSessionFiles),
        (boxline.Session, boxline.AsyncSession),
        (boxline.Files, boxline.AsyncFiles),
        (boxline.Profiles, boxline.AsyncProfiles),
        (boxline.Crawl, boxline.AsyncCrawl),
        (boxline.Agent, boxline.AsyncAgent),
        (boxline.ApiKeys, boxline.AsyncApiKeys),
        (boxline.Auth, boxline.AsyncAuth),
        (boxline.Project, boxline.AsyncProject),
    ]
    rename = {"aclose": "close", "__aenter__": "__enter__", "__aexit__": "__exit__"}
    for sync_cls, async_cls in pairs:
        public = lambda cls: {n for n in vars(cls) if not n.startswith("_")}
        assert public(sync_cls) == {rename.get(n, n) for n in public(async_cls)}, sync_cls.__name__


# ---------------------------------------------------------------- computer use, mouse and keyboard


def _ok(value: Any = None, text: str = "done") -> httpx.Response:
    return reply({"results": [{"ok": True, "action": "x", "value": value, "text": text, "ms": 1}]})


def test_mouse_keyboard_hover_cursor_actions() -> None:
    f = Fake(reply(session()), _ok({"x": 10, "y": 20}))
    s = f.sync().sessions.get(SESSION_ID)
    assert s.mouse.move(10, 20, steps=5) == {"x": 10, "y": 20}
    s.mouse.move_by(5, -5)
    s.mouse.click(10, 20, button="right", count=2, modifiers=["Shift"])
    s.mouse.down()
    s.mouse.up(button="middle")
    s.mouse.drag((1, 2), "#target", steps=20)
    s.mouse.drag(path=[(1, 2), {"x": 3, "y": 4}], steps=2)
    s.hover("#menu")
    s.hover(x=5, y=6)
    s.keyboard.key("Control+A", hold_ms=100)
    s.keyboard.key(["Control", "C"])
    s.keyboard.type("hello")
    s.cursor()
    s.click("#go", count=2)
    s.scroll(300, x=100, y=200, delta_x=50)
    assert [f.body(i)["actions"][0] for i in range(1, len(f.requests))] == [
        {"action": "move", "x": 10, "y": 20, "steps": 5},
        {"action": "move", "dx": 5, "dy": -5},
        {"action": "click", "x": 10, "y": 20, "button": "right", "count": 2, "modifiers": ["Shift"]},
        {"action": "mouse_down"},
        {"action": "mouse_up", "button": "middle"},
        {"action": "drag", "steps": 20, "from": {"x": 1, "y": 2}, "to": "#target"},
        {"action": "drag", "steps": 2, "path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]},
        {"action": "hover", "selector": "#menu"},
        {"action": "hover", "x": 5, "y": 6},
        {"action": "key", "keys": "Control+A", "holdMs": 100},
        {"action": "key", "keys": ["Control", "C"]},
        {"action": "type", "text": "hello"},
        {"action": "cursor"},
        {"action": "click", "selector": "#go", "count": 2},
        {"action": "scroll", "deltaY": 300, "deltaX": 50, "x": 100, "y": 200},
    ]
    with pytest.raises(ValueError):
        s.mouse.drag((1, 2))


def test_screenshot_options() -> None:
    f = Fake(reply(session()), _ok({"data": "aGk=", "mimeType": "image/png", "width": 640, "height": 360, "scale": 0.5}))
    s = f.sync().sessions.get(SESSION_ID)
    assert s.screenshot(max_width=640, cursor=True) == b"hi"
    assert f.body(1)["actions"][0] == {"action": "screenshot", "fullPage": False, "format": "png", "maxWidth": 640, "cursor": True}


def test_bare_string_steps_get_the_step_time_limit_and_text() -> None:
    f = Fake(reply({"results": [{"ok": True, "action": "goto", "text": "Opened https://example.com/ (HTTP 200)", "ms": 1}, {"ok": True, "action": "step", "text": "Clicked Sign in", "ms": 1}]}))
    results = f.sync(timeout=5).sessions.actions(SESSION_ID, [{"action": "goto", "url": "https://example.com"}, "click Sign in"])
    assert f.body(0) == {"actions": [{"action": "goto", "url": "https://example.com"}, "click Sign in"]}
    assert [r["text"] for r in results] == ["Opened https://example.com/ (HTTP 200)", "Clicked Sign in"]
    assert f.requests[0].extensions["timeout"]["read"] == 420.0
    g = Fake(reply({"results": [{"ok": True, "action": "step", "ms": 1}]}))
    g.sync().sessions.actions(SESSION_ID, "click Sign in")
    assert g.body(0) == {"actions": ["click Sign in"]}


def test_computer_action() -> None:
    screen = {"ok": True, "action": "left_click", "shape": "anthropic", "text": "Clicked at (256, 150)", "screenshot": "aGk=", "mimeType": "image/png", "width": 1024, "height": 576, "scale": 0.8, "cursor": {"x": 512, "y": 300}, "url": "https://example.com/", "title": "Example"}
    f = Fake(reply(session()), reply(screen))
    s = f.sync().sessions.get(SESSION_ID)
    assert s.computer({"action": "left_click", "coordinate": [512, 300]}, max_width=1024) == screen
    s.computer({"type": "keypress", "keys": ["CTRL", "A"]}, screenshot=False)
    assert f.requests[1].url.path == f"/v1/sessions/{SESSION_ID}/browser/computer"
    assert f.body(1) == {"action": "left_click", "coordinate": [512, 300], "maxWidth": 1024}
    assert f.body(2) == {"type": "keypress", "keys": ["CTRL", "A"], "screenshot": False}
    bad = Fake(api_error(400, "out_of_viewport"))
    with pytest.raises(boxline.OutOfViewportError):
        bad.sync().sessions.computer(SESSION_ID, {"type": "click", "x": 9999, "y": 1})


def test_agent_mode_and_thoughts() -> None:
    sse = b'data: {"type":"thought","text":"I will open the page.","at":"t"}\n\ndata: {"type":"done","status":"completed","result":"ok","error":null}\n\n'
    f = Fake(reply({"id": "run_1", "status": "running", "sessionId": "s", "provider": "anthropic", "model": "m", "mode": "computer"}, 201), reply(content=sse))
    bx = f.sync()
    assert bx.agent.run("t", mode="computer")["mode"] == "computer"
    assert f.body(0) == {"task": "t", "mode": "computer"}
    assert [e["text"] for e in bx.agent.stream("run_1") if e["type"] == "thought"] == ["I will open the page."]


# ---------------------------------------------------------------- web search


def test_search_posts_the_query_and_options() -> None:
    found = {"query": "boxline sdk", "results": [{"title": "Boxline", "url": "https://boxline.dev", "snippet": "Cloud browsers"}], "ms": 400, "cached": False}
    f = Fake(reply(found))
    assert f.sync().search("boxline sdk", limit=3, country="DE", language="de", recency="week", safe_search="strict", fetch=1) == found
    assert f.requests[0].url.path == "/v1/search"
    assert f.body(0) == {"query": "boxline sdk", "limit": 3, "country": "DE", "language": "de", "recency": "week", "safeSearch": "strict", "fetch": 1}
    assert "idempotency-key" not in f.requests[0].headers


def test_search_unavailable() -> None:
    f = Fake(api_error(503, "search_unavailable"))
    with pytest.raises(boxline.SearchUnavailableError) as e:
        f.sync().search("x")
    assert not e.value.retryable and len(f.requests) == 1
    later = boxline.make_error(503, "search_unavailable", "m", headers=httpx.Headers({"retry-after": "30"}))
    assert later.retryable


def test_model_refused() -> None:
    f = Fake(api_error(422, "model_refused"))
    with pytest.raises(boxline.ModelRefusedError) as e:
        f.sync().extract(url="https://example.com", prompt="x")
    assert e.value.status == 422
    g = Fake(reply(session()), reply({"results": [{"ok": False, "action": "extract", "error": "the model declined", "code": "model_refused", "ms": 1}]}))
    with pytest.raises(boxline.ModelRefusedError) as e2:
        g.sync().sessions.get(SESSION_ID).extract("everything")
    assert e2.value.status == 422


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOXLINE_API_URL", raising=False)
    assert boxline.Boxline(api_key="k").base_url == "https://api.boxline.dev"
    assert boxline.AsyncBoxline(api_key="k").base_url == "https://api.boxline.dev"
    monkeypatch.setenv("BOXLINE_API_URL", "http://localhost:8080/")
    assert boxline.Boxline(api_key="k").base_url == "http://localhost:8080"
    assert boxline.Boxline(api_key="k", base_url="http://127.0.0.1:9000").base_url == "http://127.0.0.1:9000"


def test_async_mouse_and_search() -> None:
    async def main() -> None:
        f = Fake(reply(session()), _ok({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}}), reply({"query": "q", "results": [], "ms": 1, "cached": True}))
        bx = f.async_()
        s = await bx.sessions.get(SESSION_ID)
        assert (await s.mouse.drag((1, 2), (3, 4)))["to"] == {"x": 3, "y": 4}
        assert (await bx.search("q"))["cached"] is True
        assert f.body(1)["actions"][0] == {"action": "drag", "from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}}

    run(main())


def test_session_actions_take_bare_string_steps_sync_and_async() -> None:
    import inspect as _inspect
    import typing

    results = {"results": [{"ok": True, "action": "step", "text": "Clicked Sign in", "ms": 1}]}
    f = Fake(reply(session()), reply(results))
    s = f.sync().sessions.get(SESSION_ID)
    assert s.actions(["click Sign in"])[0]["text"] == "Clicked Sign in"
    s.actions("click Sign in")
    assert f.body(1) == {"actions": ["click Sign in"]} and f.body(2) == {"actions": ["click Sign in"]}
    assert f.requests[1].extensions["timeout"]["read"] == 420.0  # a step's time limit

    async def main() -> None:
        g = Fake(reply(session()), reply(results))
        a = await g.async_().sessions.get(SESSION_ID)
        await a.actions([{"action": "goto", "url": "https://example.com"}, "click Sign in"])
        assert g.body(1) == {"actions": [{"action": "goto", "url": "https://example.com"}, "click Sign in"]}

    run(main())
    # The type says so too, on the session object as on bx.sessions (sync and async).
    for cls in (boxline.Session, boxline.AsyncSession, boxline.Sessions, boxline.AsyncSessions):
        hints = typing.get_type_hints(cls.actions, vars(_inspect.getmodule(cls)))
        assert str in typing.get_args(hints["actions"]), cls.__name__


def test_login_and_code_waits_get_the_servers_time_limit() -> None:
    # The login action and a wait for a pushed (or codeUrl) code take longer on the server than the default 120 s.
    login = {"results": [{"ok": True, "action": "login", "value": {"url": "https://shop.example.com", "title": "Shop", "runId": "run_1"}, "ms": 1}]}
    typed = {"results": [{"ok": True, "action": "type", "ms": 1}]}
    step = {"results": [{"ok": True, "action": "step", "text": "Typed the code", "ms": 1}]}
    f = Fake(reply(session()), reply(login), reply(typed), reply(step), reply(step))
    s = f.sync().sessions.get(SESSION_ID)
    s.login("SHOP")
    s.type_credential("SHOP", field="otp", selector="#code")
    s.actions("type %SHOP.otp% into the code field")
    s.actions("click Sign in")
    timeouts = [r.extensions["timeout"]["read"] for r in f.requests[1:]]
    assert timeouts == [1440.0, 960.0, 420.0 + 960.0, 420.0]
