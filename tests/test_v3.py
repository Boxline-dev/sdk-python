"""SDK 3.0.0: the one-call-per-job shapes of the redesigned API tree (docs/CONTRACT.md "The API tree")."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List

import httpx
import pytest

import boxline
from fakes import SESSION_ID, Fake, api_error, reply, session

SHELL_SESSION = session(browser=False, shell=True, connectUrl=None, liveUrl=None, terminalUrl="ws://t")


def seen(f: Fake) -> List[str]:
    return [f"{r.method} {r.url.path}" for r in f.requests]


def test_a_shell_only_session_has_no_live_or_connect_url() -> None:
    f = Fake(reply(SHELL_SESSION, 201))
    s = f.sync().sessions.create(browser=False, shell=True)
    assert f.body(0) == {"browser": False, "shell": True}
    assert s.live_url is None and s.connect_url is None and s.terminal_url == "ws://t"


def test_update_takes_timeout_rotate_proxy_and_rotate_urls_on_the_client_and_the_handle() -> None:
    f = Fake(reply(session(timeout=900)))
    bx = f.sync()
    bx.sessions.update(SESSION_ID, timeout=900, rotate_proxy=True, rotate_urls=True, user_metadata={"a": 1})
    s = bx.sessions.get(SESSION_ID)
    s.update(rotate_urls=True)
    assert seen(f) == [f"PATCH /v1/sessions/{SESSION_ID}", f"GET /v1/sessions/{SESSION_ID}", f"PATCH /v1/sessions/{SESSION_ID}"]
    assert f.body(0) == {"timeout": 900, "userMetadata": {"a": 1}, "rotateProxy": True, "rotateUrls": True}
    assert f.body(2) == {"rotateUrls": True}


def test_a_field_fixed_at_create_is_a_not_updatable_error() -> None:
    f = Fake(api_error(400, "not_updatable"))
    with pytest.raises(boxline.NotUpdatableError) as e:
        f.sync().sessions.update(SESSION_ID, keep_alive=True)
    assert e.value.status == 400 and e.value.code == "not_updatable"
    with pytest.raises(TypeError):
        f.sync().sessions.update(SESSION_ID, viewport={"width": 800, "height": 600})  # type: ignore[call-arg]


def test_calls_the_canonical_browser_shell_and_script_paths() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/shell/exec"):
            return reply({"stdout": "ok\n", "stderr": "", "exitCode": 0, "timedOut": False, "truncated": False, "durationMs": 1})
        if p.endswith("/browser/script"):
            return reply(content=b'{"type":"exit","exitCode":0}\n')
        if p.endswith("/browser/actions"):
            return reply({"results": [{"ok": True, "action": "goto", "value": None, "ms": 1}]})
        if p.endswith("/browser/export-cookies"):
            return reply({"path": "cookies.txt", "count": 2})
        return reply(session())

    f = Fake(answer)
    s = f.sync().sessions.get(SESSION_ID)
    assert s.exec("echo ok")["stdout"] == "ok\n"
    s.run_script("console.log(1)").wait()
    s.goto("https://example.com")
    s.export_cookies()
    assert seen(f)[1:] == [
        f"POST /v1/sessions/{SESSION_ID}/shell/exec",
        f"POST /v1/sessions/{SESSION_ID}/browser/script",
        f"POST /v1/sessions/{SESSION_ID}/browser/actions",
        f"POST /v1/sessions/{SESSION_ID}/browser/export-cookies",
    ]


def test_typed_errors_for_the_new_codes() -> None:
    cases = [
        (409, "browser_disabled", boxline.BrowserDisabledError),
        (409, "shell_disabled", boxline.ShellDisabledError),
        (409, "nothing_saved", boxline.NothingSavedError),
        (409, "not_resumable", boxline.NotResumableError),
        (400, "not_updatable", boxline.NotUpdatableError),
        (413, "archive_too_large", boxline.ArchiveTooLargeError),
        (400, "invalid_path", boxline.InvalidPathError),
        (403, "invalid_path", boxline.InvalidPathError),
        (400, "not_a_directory", boxline.NotADirectoryError),
    ]
    for status, code, cls in cases:
        err = boxline.make_error(status, code, "m")
        assert isinstance(err, cls), code
        assert not err.retryable, code
    busy = boxline.make_error(429, "archive_busy", "m", headers=httpx.Headers({"retry-after": "2"}))
    assert isinstance(busy, boxline.ArchiveBusyError) and isinstance(busy, boxline.RateLimitError)
    assert busy.retry_after == 2 and busy.retryable
    assert boxline.NotADirectoryError is not NotADirectoryError  # ours, not the builtin
    assert "NotADirectoryError" not in boxline.__all__  # `from boxline import *` must not shadow the builtin


def test_lists_use_after_only() -> None:
    f = Fake(reply({"data": [SHELL_SESSION], "total": 1, "limit": 50, "next": None}))
    page = f.sync().sessions.list(kind="shell", limit=50)
    assert str(f.requests[0].url.params) == "kind=shell&limit=50"
    assert page.total == 1
    with pytest.raises(TypeError):
        f.sync().sessions.list(offset=10)  # type: ignore[call-arg]


def test_archive_returns_the_bytes_on_the_client_and_the_handle_also_for_a_stopped_session() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files/archive"):
            return reply(content=b"\x1f\x8b\x08", headers={"content-type": "application/octet-stream"})
        return reply(session(status="STOPPED"))

    f = Fake(answer)
    bx = f.sync()
    assert bx.sessions.files.archive(SESSION_ID, "results") == b"\x1f\x8b\x08"
    s = bx.sessions.get(SESSION_ID)
    s.files.archive()
    s.files.archive("a b/c")
    queries = [str(r.url.params) for r in f.requests if r.url.path.endswith("/archive")]
    assert queries == ["path=results", "", "path=a+b%2Fc"]


def test_reads_on_a_stopped_session_are_the_same_calls_and_nothing_saved_is_typed() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list"):
            return reply({"path": ".", "entries": [{"name": "out", "type": "dir", "size": 0, "mtime": "t"}, {"name": "link", "type": "symlink", "size": 0, "mtime": "t"}]})
        if request.url.params.get("path") == "gone":
            return api_error(409, "nothing_saved")
        return reply(content=b"hello")

    f = Fake(answer)
    bx = f.sync()
    assert [e["type"] for e in bx.sessions.files.list("stopped")] == ["dir", "symlink"]
    assert bx.sessions.files.read_text("stopped", "report.txt") == "hello"
    with pytest.raises(boxline.NothingSavedError):
        bx.sessions.files.read("stopped", "gone")
    assert seen(f) == ["GET /v1/sessions/stopped/files"] * 3


def test_agent_run_takes_a_session_and_the_flat_session_fields_are_gone() -> None:
    f = Fake(reply({"id": "run_1", "status": "running", "sessionId": "s1", "provider": "anthropic", "model": "m", "keySource": "platform", "mode": "tools"}, 201))
    bx = f.sync()
    bx.agent.run("build it", session={"browser": False, "shell": True, "setup": ["pip install x"], "timeout": 1200}, keep_session=True)
    bx.agent.run("sign in", session={"proxy": True, "captcha": "solve", "profile": {"id": "prof_1", "persist": True}}, credentials=["SHOP"], allow_with_extensions=False)
    assert f.body(0) == {"task": "build it", "session": {"browser": False, "shell": True, "setup": ["pip install x"], "timeout": 1200}, "keepSession": True}
    assert f.body(1) == {"task": "sign in", "session": {"proxy": True, "captcha": "solve", "profile": {"id": "prof_1", "persist": True}}, "credentials": ["SHOP"], "allowWithExtensions": False}
    for gone in ("proxy", "browser", "shell", "block_ads", "profile", "timeout", "idle_timeout", "cookie_banners", "extensions", "captcha"):
        with pytest.raises(TypeError):
            bx.agent.run("x", **{gone: True})  # type: ignore[arg-type]


def test_pause_and_resume_use_the_canonical_paths_and_resume_goes_on_with_the_same_run() -> None:
    def run(status: str, **extra: Any) -> Dict[str, Any]:
        return {"id": "run_1", "status": status, "task": "t", "sessionId": "s1", "steps": [], "result": None, "error": None, "usage": {"inputTokens": 0, "outputTokens": 0, "costUsd": 0}, "handover": None, "createdAt": "t", "finishedAt": None, **extra}

    f = Fake(reply(run("paused", handover={"by": "user", "reason": "a person needs to look"})), reply(run("running", resumable=None)))
    bx = f.sync()
    assert bx.agent.pause("run_1", "a person needs to look")["status"] == "paused"
    resumed = bx.agent.resume("run_1", note="I signed in", max_steps=40, max_cost_usd=2, variables={"PASSWORD": "x"})
    assert resumed["id"] == "run_1" and resumed["status"] == "running"
    bx.agent.resume("run_1")
    assert seen(f) == ["POST /v1/agent/runs/run_1/pause", "POST /v1/agent/runs/run_1/resume", "POST /v1/agent/runs/run_1/resume"]
    assert f.body(0) == {"reason": "a person needs to look"}
    assert f.body(1) == {"note": "I signed in", "variables": {"PASSWORD": "x"}, "maxSteps": 40, "maxCostUsd": 2}
    assert f.body(2) == {}
    for gone in ("takeover", "hand_back", "continue_run"):
        assert not hasattr(bx.agent, gone), gone


def test_a_refused_resume_is_not_resumable_and_a_run_shows_resumable() -> None:
    until = "2026-10-07T12:10:00.000Z"
    f = Fake(reply({"id": "run_1", "status": "failed", "errorCode": "max_steps", "resumable": {"until": until}}), api_error(409, "not_resumable"))
    bx = f.sync()
    assert bx.agent.get("run_1")["resumable"] == {"until": until}  # type: ignore[typeddict-item]
    with pytest.raises(boxline.NotResumableError):
        bx.agent.resume("run_1", max_steps=60)


def test_a_run_streams_from_events_stream() -> None:
    f = Fake(reply(content=b'data: {"type":"done","status":"failed","result":null,"error":"x","errorCode":"max_steps","resumable":{"until":"t"}}\n\n', headers={"content-type": "text/event-stream"}))
    events = list(f.sync().agent.stream("run_1"))
    assert events[0]["resumable"] == {"until": "t"}
    assert f.requests[0].url.path == "/v1/agent/runs/run_1/events/stream"


def test_tasks_take_a_session_and_read_it_back_with_env_as_names() -> None:
    task = {"id": "task_1", "name": "n", "instruction": "i", "variables": [], "credentials": [], "output": None, "session": {"browser": False, "shell": True, "env": ["REGION"]}, "allowWithExtensions": False}
    f = Fake(reply(task, 201))
    bx = f.sync()
    created = bx.tasks.create("n", "i", session={"browser": False, "shell": True, "env": {"REGION": "eu"}}, allow_with_extensions=False)
    assert created["session"] == {"browser": False, "shell": True, "env": ["REGION"]}  # type: ignore[comparison-overlap]
    bx.tasks.update("task_1", session=None, allow_with_extensions=True)
    assert f.body(0) == {"name": "n", "instruction": "i", "session": {"browser": False, "shell": True, "env": {"REGION": "eu"}}, "allowWithExtensions": False}
    assert f.body(1) == {"allowWithExtensions": True, "session": None}
    for gone in ("browser", "profile"):
        with pytest.raises(TypeError):
            bx.tasks.create("n", "i", **{gone: {}})  # type: ignore[arg-type]


def test_usage_is_the_one_call_with_what_stats_had() -> None:
    f = Fake(reply({"from": "a", "to": "b", "sessions": 3, "running": 1, "concurrencyLimit": 10, "agentRuns": 4, "modelCostUsd": 0.5, "ownKeyModelCostUsd": 0.25, "byDay": [{"date": "2026-10-07", "agentRuns": 4}]}))
    bx = f.sync()
    got = bx.usage(from_="2026-10-01", to="2026-10-07")
    assert f.requests[0].url.path == "/v1/project/usage" and str(f.requests[0].url.params) == "from=2026-10-01&to=2026-10-07"
    assert (got["running"], got["concurrencyLimit"], got["agentRuns"], got["modelCostUsd"], got["ownKeyModelCostUsd"]) == (1, 10, 4, 0.5, 0.25)
    assert not hasattr(bx, "stats")


def test_the_other_moved_calls() -> None:
    f = Fake(lambda r: reply(None, 204) if r.method == "DELETE" else reply({"id": "x", "data": [], "next": None, "all": "*"}))
    bx = f.sync()
    bx.crawl.start("https://example.com")
    bx.crawl.get("c1", limit=0)
    bx.crawl.list()
    bx.crawl.cancel("c1")
    bx.api_keys.create("k")
    bx.api_keys.list()
    bx.api_keys.revoke("k1")
    bx.webhooks.event_types()
    assert seen(f) == [
        "POST /v1/crawls",
        "GET /v1/crawls/c1",
        "GET /v1/crawls",
        "POST /v1/crawls/c1/cancel",
        "POST /v1/project/api-keys",
        "GET /v1/project/api-keys",
        "DELETE /v1/project/api-keys/k1",
        "GET /v1/webhooks/event-types",
    ]


def test_the_removed_session_methods_are_gone() -> None:
    f = Fake(reply(session()))
    bx = f.sync()
    s = bx.sessions.get(SESSION_ID)
    for name in ("extend", "rotate_proxy", "rotate_urls", "live", "move", "set_proxy"):
        assert not hasattr(s, name), f"session.{name}"
    for name in ("extend", "rotate_proxy", "rotate_urls", "live", "move", "page"):
        assert not hasattr(bx.sessions, name), f"sessions.{name}"
    assert "moves" not in boxline.types.SessionData.__annotations__


# ---------------------------------------------------------------- session.on_captcha


def captcha_event(seq: int, state: str, kind: str = "recaptcha") -> Dict[str, Any]:
    return {"seq": seq, "at": "t", "type": "captcha", "text": f"captcha {state}", "url": "https://example.com/login", "data": {"kind": kind, "state": state}}


def captcha_api(seen_params: List[Any]) -> Any:
    events = [captcha_event(1, "detected"), captcha_event(2, "cleared"), {"seq": 3, "at": "t", "type": "captcha", "data": {"kind": "recaptcha", "state": "solving"}}]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            seen_params.append(dict(request.url.params))
            after = int(request.url.params.get("after", 0))
            fresh = [e for e in events if e["seq"] > after]
            return reply({"data": fresh, "nextAfter": 3, "next": None})
        return reply(session())

    return answer


def test_on_captcha_reports_detected_and_cleared_and_stops_when_asked() -> None:
    params: List[Any] = []
    f = Fake(captcha_api(params))
    s = f.sync().sessions.get(SESSION_ID)
    changes: List[Any] = []
    got = threading.Event()

    def handler(change: Any) -> None:
        changes.append(change)
        if len(changes) == 2:
            got.set()

    stop = s.on_captcha(handler, interval=0.01)
    assert got.wait(5), "the handler was not called"
    for _ in range(500):  # a second poll, which starts after what the first one saw
        if len(params) >= 2:
            break
        time.sleep(0.01)
    stop()
    time.sleep(0.05)
    assert [(c["state"], c["kind"], c["url"]) for c in changes] == [("detected", "recaptcha", "https://example.com/login"), ("cleared", "recaptcha", "https://example.com/login")]
    assert changes[0]["event"]["seq"] == 1
    # The first poll starts after event 0, the next after what was seen (nextAfter), and only captcha events are asked for.
    assert params[0] == {"types": "captcha", "after": "0"} and params[1]["after"] == "3"
    done = len(changes)
    time.sleep(0.05)
    assert len(changes) == done


def test_on_captcha_in_the_async_client_takes_a_coroutine_handler_and_stops_when_asked() -> None:
    params: List[Any] = []
    f = Fake(captcha_api(params))

    async def go() -> List[Any]:
        s = await f.async_().sessions.get(SESSION_ID)
        changes: List[Any] = []

        async def handler(change: Any) -> None:
            await asyncio.sleep(0)
            changes.append(change)

        stop = s.on_captcha(handler, interval=0.01, after=1)
        for _ in range(200):
            if changes:
                break
            await asyncio.sleep(0.01)
        stop()
        await asyncio.sleep(0.05)
        return changes

    changes = asyncio.run(go())
    # Started after event 1: the "cleared" one is the first this subscriber sees.
    assert [c["state"] for c in changes] == ["cleared"]
    assert params[0]["after"] == "1"


def test_on_captcha_stops_by_itself_when_the_session_ends() -> None:
    calls = {"events": 0}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            calls["events"] += 1
            return reply({"data": [], "nextAfter": 0, "next": None})
        return reply(session(status="STOPPED"))

    f = Fake(answer)
    s = f.sync().sessions.get(SESSION_ID)
    stop = s.on_captcha(lambda c: None, interval=0.01)
    time.sleep(0.3)
    stop()
    # The first poll sees the session is not RUNNING and the watcher ends: no endless polling afterwards.
    assert calls["events"] <= 6
