"""Tasks (docs/CONTRACT.md "Tasks", "Schedules") and structured output ("Structured output") in both clients, on a fake
HTTP layer: the requests each method sends, cursor pages, retries with one Idempotency-Key, wait_for_run (the API has
no GET for one task run) and the typed errors."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx
import pytest

import boxline
from fakes import NO_WAIT, Fake, api_error, reply

TASK: Dict[str, Any] = {
    "id": "task_1",
    "name": "Books",
    "instruction": "Open https://books.toscrape.com and list the books in %category%",
    "variables": [{"name": "category", "default": "Travel"}],
    "credentials": ["SHOP"],
    "output": {"type": "object", "properties": {"books": {"type": "array"}}, "required": ["books"]},
    "browser": None,
    "profile": None,
    "model": None,
    "maxSteps": None,
    "notifyOnFailure": None,
    "schedule": {"cron": "0 9 * * MON-FRI", "timezone": "Europe/London", "variables": {}, "enabled": True, "nextRunAt": "2026-10-01T08:00:00.000Z"},
    "lastRunAt": None,
    "createdAt": "2026-09-30T00:00:00.000Z",
    "updatedAt": "2026-09-30T00:00:00.000Z",
}

OPEN = "queued,running,paused"


def run(status: str, **extra: Any) -> Dict[str, Any]:
    return {
        "id": "trun_1",
        "taskId": "task_1",
        "runId": "run_1",
        "sessionId": "s1",
        "status": status,
        "scheduled": False,
        "scheduledFor": None,
        "reason": None,
        "missedCount": None,
        "variables": {"category": "Poetry"},
        "secretVariables": [],
        "structured": True,
        "result": None,
        "resultText": None,
        "error": None,
        "errorCode": None,
        "usage": {"inputTokens": 0, "outputTokens": 0, "costUsd": 0},
        "durationMs": None,
        "createdAt": "2026-09-30T00:00:00.000Z",
        "finishedAt": None,
        **extra,
    }


def page(data: List[Any], nxt: Optional[str] = None) -> httpx.Response:
    return reply({"data": data, "next": nxt})


def q(request: httpx.Request) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}


def test_methods_send_the_requests_the_api_expects() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return reply(None, 204)
        if request.method == "GET" and request.url.path.endswith("/runs"):
            return page([run("completed")])
        if request.method == "GET" and request.url.path == "/v1/tasks":
            return page([TASK])
        if request.url.path.endswith("/runs"):
            return reply(run("running"), 201)
        return reply(TASK, 201 if request.method == "POST" else 200)

    for flavour in ("sync", "async"):
        f = Fake(answer)

        async def calls(bx: Any, aw: Any) -> None:
            created = await aw(
                bx.tasks.create(
                    "Books",
                    TASK["instruction"],
                    variables=[{"name": "category", "default": "Travel"}, {"name": "password", "secret": True, "origins": ["https://example.com"]}],
                    output=TASK["output"],
                    browser={"blockAds": True, "cookieBanners": "reject", "locale": "en-GB"},
                    profile={"id": "prof_1", "persist": False},
                    model={"provider": "anthropic", "model": "claude-haiku-4-5"},
                    max_steps=10,
                    schedule={"cron": "0 9 * * MON-FRI", "timezone": "Europe/London"},
                    credentials=("SHOP",),
                )
            )
            assert created["schedule"]["nextRunAt"] == TASK["schedule"]["nextRunAt"]
            assert (await aw(bx.tasks.list(limit=5))).data == [TASK]
            await aw(bx.tasks.get("task/1"))
            await aw(bx.tasks.update("task_1", schedule={"enabled": False}, output=None, max_steps=None, credentials=None))
            await aw(bx.tasks.update("task_1", variables=[], credentials=["SHOP"]))
            await aw(bx.tasks.delete("task_1"))
            started = await aw(bx.tasks.run("task_1", variables={"category": "Poetry", "pages": 2}))
            assert started["status"] == "running"
            await aw(bx.tasks.run("task_1"))
            history = await aw(bx.tasks.runs("task_1", status=["failed", "skipped", "missed"], limit=20))
            assert history.data[0]["status"] == "completed"
            await aw(bx.tasks.runs("task_1", status="queued"))

        if flavour == "sync":

            async def sync_await(v: Any) -> Any:
                return v

            asyncio.run(calls(f.sync(), sync_await))
        else:

            async def async_await(v: Any) -> Any:
                return await v

            asyncio.run(calls(f.async_(), async_await))

        seen = [f"{r.method} {r.url.raw_path.decode()}" for r in f.requests]
        assert seen == [
            "POST /v1/tasks",
            "GET /v1/tasks?limit=5",
            "GET /v1/tasks/task%2F1",
            "PATCH /v1/tasks/task_1",
            "PATCH /v1/tasks/task_1",
            "DELETE /v1/tasks/task_1",
            "POST /v1/tasks/task_1/runs",
            "POST /v1/tasks/task_1/runs",
            "GET /v1/tasks/task_1/runs?status=failed%2Cskipped%2Cmissed&limit=20",
            "GET /v1/tasks/task_1/runs?status=queued",
        ], flavour
        body = f.body(0)
        assert body["name"] == "Books" and body["output"] == TASK["output"] and body["profile"] == {"id": "prof_1", "persist": False}
        assert body["maxSteps"] == 10 and body["browser"] == {"blockAds": True, "cookieBanners": "reject", "locale": "en-GB"}
        assert body["variables"][1] == {"name": "password", "secret": True, "origins": ["https://example.com"]}
        assert "notifyOnFailure" not in body and body["credentials"] == ["SHOP"]
        # None removes a field: it reaches the API as null; fields not passed are left out.
        assert f.body(3) == {"schedule": {"enabled": False}, "output": None, "maxSteps": None, "credentials": None}
        assert f.body(4) == {"variables": [], "credentials": ["SHOP"]}
        assert f.body(6) == {"variables": {"category": "Poetry", "pages": 2}}
        assert f.body(7) == {}
        keys = [r.headers.get("idempotency-key") for r in f.requests]
        assert keys[0] and keys[6] and keys[7] and keys[6] != keys[7]
        assert [k for i, k in enumerate(keys) if f.requests[i].method != "POST"] == [None] * 7


def test_a_task_run_is_retried_with_one_key_and_an_update_never() -> None:
    f = Fake(api_error(503, "no_capacity", NO_WAIT), reply(run("running"), 201))
    f.sync().tasks.run("task_1", variables={"category": "Poetry"}, options={"idempotency_key": "job-7"})
    assert [r.headers["idempotency-key"] for r in f.requests] == ["job-7", "job-7"]
    g = Fake(api_error(503, "no_capacity", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        g.sync().tasks.update("task_1", name="x")
    assert len(g.requests) == 1


def test_lists_follow_next() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        after = q(request).get("after")
        if request.url.path == "/v1/tasks":
            return page([{**TASK, "id": "task_2"}]) if after else page([TASK], "c2")
        return page([run("missed", id="trun_2")]) if after else page([run("completed")], "c2")

    f = Fake(answer)
    bx = f.sync()
    assert [t["id"] for t in bx.tasks.list(limit=1)] == ["task_1", "task_2"]
    assert [f"{r['id']}:{r['status']}" for r in bx.tasks.runs("task_1", status=["completed", "missed"])] == ["trun_1:completed", "trun_2:missed"]

    async def go() -> List[str]:
        return [r["id"] async for r in f.async_().tasks.runs("task_1")]

    assert asyncio.run(go()) == ["trun_1", "trun_2"]


def waiting_api(polls: List[int], books: Any) -> Any:
    def answer(request: httpx.Request) -> httpx.Response:
        if q(request).get("status") == OPEN:
            assert q(request)["limit"] == "100"
            polls.append(1)
            # A scheduled run waits its turn (queued, no agent run yet), then runs, then is gone from the unfinished ones.
            if len(polls) == 1:
                return page([run("queued", runId=None, scheduled=True)])
            if len(polls) == 2:
                return page([run("running")])
            return page([])
        return page([run("failed", id="trun_0"), run("completed", result=books, resultText="Found 1 book.")])

    return answer


def test_wait_for_run_polls_the_unfinished_runs_then_reads_the_history() -> None:
    books = {"books": [{"title": "A Light in the Attic", "price": 51.77}]}
    polls: List[int] = []
    f = Fake(waiting_api(polls, books))
    done = f.sync().tasks.wait_for_run(run("running"), poll=0)
    assert done["status"] == "completed" and done["result"] == books
    assert [q(r).get("status") for r in f.requests] == [OPEN, OPEN, OPEN, None]

    polls.clear()
    g = Fake(waiting_api(polls, books))
    done = asyncio.run(g.async_().tasks.wait_for_run("task_1", "trun_1", poll=0))
    assert done["result"] == books and len(polls) == 3


def test_wait_for_run_keeps_waiting_while_paused_and_walks_history_pages() -> None:
    polls: List[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        params = q(request)
        if params.get("status") == OPEN:
            polls.append(1)
            return page([run("paused")] if len(polls) < 3 else [run("running", id="trun_other")])
        return page([run("canceled", error="stopped by the user")]) if params.get("after") else page([run("completed", id="trun_9")], "c2")

    done = Fake(answer).sync().tasks.wait_for_run("task_1", "trun_1", poll=0)
    assert done["status"] == "canceled" and done["error"] == "stopped by the user" and len(polls) == 3


def test_wait_for_run_times_out_and_reports_a_missing_run() -> None:
    with pytest.raises(boxline.BoxlineTimeoutError):
        Fake(page([run("running")])).sync().tasks.wait_for_run("task_1", "trun_1", poll=0.005, timeout=0.02)
    with pytest.raises(boxline.NotFoundError) as e:
        Fake(page([])).sync().tasks.wait_for_run("task_1", "trun_nope")
    assert "trun_nope" in e.value.message
    with pytest.raises(boxline.NotFoundError):
        asyncio.run(Fake(api_error(404, "not_found")).async_().tasks.wait_for_run("task_gone", "trun_1"))
    with pytest.raises(TypeError):
        Fake(page([])).sync().tasks.wait_for_run("task_1")


def test_agent_run_sends_output_and_reads_the_json_answer() -> None:
    schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    started = {"id": "run_1", "status": "running", "sessionId": "s1", "provider": "anthropic", "model": "claude-haiku-4-5", "mode": "tools"}
    finished = {"id": "run_1", "status": "completed", "result": {"title": "Example Domain"}, "resultText": "Returned the title.", "output": schema, "errorCode": None, "steps": []}

    def answer(request: httpx.Request) -> httpx.Response:
        return reply(started, 201) if request.method == "POST" else reply(finished)

    for flavour in ("sync", "async"):
        f = Fake(answer)
        if flavour == "sync":
            bx = f.sync()
            s = bx.agent.run("the page title of https://example.com", output=schema)
            done = bx.agent.wait(s["id"], poll=0)
        else:

            async def go() -> Any:
                abx = f.async_()
                s = await abx.agent.run("the page title of https://example.com", output=schema)
                return await abx.agent.wait(s["id"], poll=0)

            done = asyncio.run(go())
        assert f.body(0) == {"task": "the page title of https://example.com", "output": schema}
        assert f.requests[0].headers.get("idempotency-key")
        assert done["result"] == {"title": "Example Domain"} and done["resultText"] == "Returned the title."


def test_error_codes_and_classes() -> None:
    assert boxline.ErrorCode.OUTPUT_INVALID == "output_invalid"
    missing = boxline.make_error(400, "missing_variables", "the run needs values for: %category%")
    assert isinstance(missing, boxline.MissingVariablesError) and not missing.retryable
    limit = boxline.make_error(402, "plan_limit", "the Free plan does not include schedules")
    assert isinstance(limit, boxline.PlanLimitError) and not limit.retryable
    f = Fake(api_error(400, "missing_variables"))
    with pytest.raises(boxline.MissingVariablesError):
        f.sync().tasks.run("task_1")
    assert len(f.requests) == 1
    assert json.loads(f.requests[0].content) == {}
