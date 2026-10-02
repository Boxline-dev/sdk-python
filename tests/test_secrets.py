"""Project secrets, saved login details and the shell's env and secrets (docs/CONTRACT.md "Project secrets", "Saved login
details", "Shell environment and secrets", "Plain-English steps", "Scripts") in both clients, on a fake HTTP layer: the
requests each method sends, that creating a secret is never retried, and the typed errors."""

from __future__ import annotations

import asyncio
from typing import Any, List

import httpx
import pytest

import boxline
from fakes import NO_WAIT, SESSION_ID, Fake, api_error, reply, session

SECRET = {
    "name": "GITHUB_TOKEN",
    "description": "CI bot",
    "origins": None,
    "shell": False,
    "scope": "shell",
    "preview": "••••1a2b",
    "createdAt": "2026-09-30T00:00:00.000Z",
    "updatedAt": "2026-09-30T00:00:00.000Z",
    "lastUsedAt": None,
}


def seen(f: Fake) -> List[str]:
    return [f"{r.method} {r.url.raw_path.decode()}" for r in f.requests]


def secrets_api(request: httpx.Request) -> httpx.Response:
    if request.method == "DELETE":
        return reply(None, 204)
    if request.url.path == "/v1/secrets/audit":
        return reply({"data": [{"at": "t", "action": "use", "kind": "secret", "name": "GITHUB_TOKEN", "actor": None, "usedBy": {"type": "exec", "id": "s1"}, "details": {}}], "next": None})
    if request.method == "GET" and request.url.path == "/v1/secrets":
        return reply({"data": [SECRET], "next": None})
    return reply(SECRET, 201 if request.method == "POST" else 200)


def test_secrets_methods_send_the_requests_the_api_expects() -> None:
    f = Fake(secrets_api)
    bx = f.sync()
    assert bx.secrets.create("GITHUB_TOKEN", "v-123", description="CI bot", scope="shell") == SECRET
    assert bx.secrets.list(limit=10).data == [SECRET]
    bx.secrets.get("GITHUB/TOKEN")
    bx.secrets.update("GITHUB_TOKEN", value="v-456", description=None, origins=None, shell=True, scope="all")
    bx.secrets.update("GITHUB_TOKEN", origins=["https://example.com"])
    assert bx.secrets.audit(name="GITHUB_TOKEN", limit=5).data[0]["usedBy"] == {"type": "exec", "id": "s1"}
    bx.secrets.delete("GITHUB_TOKEN")
    assert seen(f) == [
        "POST /v1/secrets",
        "GET /v1/secrets?limit=10",
        "GET /v1/secrets/GITHUB%2FTOKEN",
        "PATCH /v1/secrets/GITHUB_TOKEN",
        "PATCH /v1/secrets/GITHUB_TOKEN",
        "GET /v1/secrets/audit?name=GITHUB_TOKEN&limit=5",
        "DELETE /v1/secrets/GITHUB_TOKEN",
    ]
    assert f.body(0) == {"name": "GITHUB_TOKEN", "value": "v-123", "description": "CI bot", "scope": "shell"}
    # None clears the description and allows any site: it reaches the API as null; fields not passed are left out.
    assert f.body(3) == {"value": "v-456", "description": None, "origins": None, "shell": True, "scope": "all"}
    assert f.body(4) == {"origins": ["https://example.com"]}
    assert all("idempotency-key" not in r.headers for r in f.requests)


def test_async_secrets_and_pages() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        return reply({"data": [{**SECRET, "name": "B" if after else "A"}], "next": None if after else "c2"})

    async def go() -> List[str]:
        abx = Fake(answer).async_()
        return [s["name"] async for s in abx.secrets.list(limit=1)]

    assert asyncio.run(go()) == ["A", "B"]
    f = Fake(secrets_api)
    assert asyncio.run(f.async_().secrets.get("GITHUB_TOKEN")) == SECRET


def test_creating_a_secret_is_never_retried_and_its_errors_are_typed() -> None:
    f = Fake(api_error(503, "unavailable", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        f.sync().secrets.create("A_TOKEN", "x-1")
    assert len(f.requests) == 1
    with pytest.raises(boxline.SecretExistsError):
        Fake(api_error(409, "secret_exists")).sync().secrets.create("A_TOKEN", "x-1")
    with pytest.raises(boxline.PlanLimitError):
        Fake(api_error(402, "plan_limit")).sync().secrets.create("A_TOKEN", "x-1")


def test_login_details() -> None:
    login = {"origin": "https://shop.example.com", "username": "ada@example.com", "hasPassword": True, "hasTotp": True, "updatedAt": "t"}
    f = Fake(lambda r: reply(None, 204) if r.method == "DELETE" else reply({"id": "ctx_1", "name": "shop", "login": login}))
    bx = f.sync()
    assert bx.contexts.set_login("ctx_1", "https://shop.example.com", "ada@example.com", "pw-1", totp_secret="JBSWY3DPEHPK3PXP")["login"] == login
    bx.contexts.set_login("ctx_1", "https://shop.example.com", "ada@example.com", "pw-1")
    bx.contexts.delete_login("ctx_1")
    assert seen(f) == ["PUT /v1/contexts/ctx_1/login", "PUT /v1/contexts/ctx_1/login", "DELETE /v1/contexts/ctx_1/login"]
    assert f.body(0) == {"origin": "https://shop.example.com", "username": "ada@example.com", "password": "pw-1", "totpSecret": "JBSWY3DPEHPK3PXP"}
    assert "totpSecret" not in f.body(1)


def test_update_login_sends_only_what_changes() -> None:
    login = {"origin": "https://shop.example.com", "username": "ada@example.com", "hasPassword": True, "hasTotp": False, "updatedAt": "t"}
    f = Fake(lambda r: reply({"id": "ctx_1", "name": "shop", "login": login}))
    bx = f.sync()
    assert bx.contexts.update_login("ctx_1", password="pw-2")["login"] == login
    bx.contexts.update_login("ctx_1", origin="https://new.example.com", password="pw-3", totp_secret=None)
    assert seen(f) == ["PATCH /v1/contexts/ctx_1/login", "PATCH /v1/contexts/ctx_1/login"]
    assert f.body(0) == {"password": "pw-2"}
    assert f.body(1) == {"origin": "https://new.example.com", "password": "pw-3", "totpSecret": None}


def test_env_and_secrets_on_sessions_and_exec() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/exec"):
            if b'"stream":true' in request.content:
                return reply(content=b'{"type":"stdout","data":"%API_KEY%\\n"}\n{"type":"exit","exitCode":0}\n')
            return reply({"stdout": "%API_KEY%\n", "stderr": "", "exitCode": 0, "timedOut": False, "durationMs": 1})
        return reply(session(env=["REGION"], secrets=["GITHUB_TOKEN"]), 201)

    f = Fake(answer)
    bx = f.sync()
    s = bx.sessions.create(shell=True, env={"REGION": "eu", "DEBUG": True}, secrets=["GITHUB_TOKEN"])
    assert s.data["env"] == ["REGION"] and s.data["secrets"] == ["GITHUB_TOKEN"]
    s.exec("echo $API_KEY", env={"MODE": "x"}, secrets=["API_KEY"])
    bx.sessions.exec(SESSION_ID, "echo $API_KEY", secrets=["API_KEY"])
    with s.exec_stream("echo $API_KEY", secrets=["API_KEY"]) as proc:
        assert [text for _, text in proc] == ["%API_KEY%\n"]
    assert f.body(0) == {"browser": True, "shell": True, "env": {"REGION": "eu", "DEBUG": True}, "secrets": ["GITHUB_TOKEN"]}
    assert f.requests[0].headers.get("idempotency-key")
    assert f.body(1) == {"command": "echo $API_KEY", "env": {"MODE": "x"}, "secrets": ["API_KEY"]}
    assert f.body(2) == {"command": "echo $API_KEY", "secrets": ["API_KEY"]}
    assert f.body(3) == {"command": "echo $API_KEY", "secrets": ["API_KEY"], "stream": True}


def test_secrets_on_steps_scripts_and_agent_runs() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/scripts/run"):
            return reply(content=b'{"type":"exit","exitCode":0}\n')
        if p.endswith("/actions"):
            return reply({"results": [{"ok": True, "action": "step", "value": {"method": "fill"}, "ms": 1}]})
        if p == "/v1/agent/runs":
            return reply({"id": "run_1", "status": "running", "sessionId": "s1", "provider": "openai", "model": "m", "mode": "tools"}, 201)
        return reply(session())

    for flavour in ("sync", "async"):
        f = Fake(answer)

        async def calls(bx: Any, aw: Any) -> None:
            s = await aw(bx.sessions.get(SESSION_ID))
            await aw(s.step("type %SITE_PASSWORD% into the password field", secrets=["SITE_PASSWORD"], allow_with_extensions=True))
            proc = await aw(s.run_script("await step('sign in')", secrets=["SITE_PASSWORD"], login=True, allow_with_extensions=True))
            await aw(proc.wait())
            await aw(bx.agent.run("sign in", secrets=["SITE_PASSWORD"], context="ctx_1", allow_with_extensions=True))
            await aw(bx.agent.run("sign in", context={"id": "ctx_1", "persist": True}))

        async def plain(v: Any) -> Any:
            return v

        async def awaited(v: Any) -> Any:
            return await v

        asyncio.run(calls(f.sync(), plain) if flavour == "sync" else calls(f.async_(), awaited))
        assert f.body(1) == {"actions": [{"action": "step", "instruction": "type %SITE_PASSWORD% into the password field", "secrets": ["SITE_PASSWORD"], "allowWithExtensions": True}]}, flavour
        assert f.body(2) == {"code": "await step('sign in')", "secrets": ["SITE_PASSWORD"], "login": True, "allowWithExtensions": True}
        assert f.body(3) == {"task": "sign in", "secrets": ["SITE_PASSWORD"], "context": {"id": "ctx_1"}, "allowWithExtensions": True}
        assert f.body(4) == {"task": "sign in", "context": {"id": "ctx_1", "persist": True}}


def test_move_returns_where_the_shell_continues() -> None:
    shell = {"cwd": "/workspace/project", "exported": ["NODE_ENV"], "stoppedProcesses": [{"pid": 42, "command": "npm run dev", "seconds": 90}]}
    timings = {"captureMs": 1, "acquireMs": 2, "restoreMs": 3, "totalMs": 6}
    assert Fake(reply({"session": session(), "timings": timings, "shell": shell})).sync().sessions.move(SESSION_ID)["shell"] == shell
    assert Fake(reply({"session": session(), "timings": timings})).sync().sessions.move(SESSION_ID)["shell"] is None


def test_secret_error_codes() -> None:
    cases = [
        (409, "too_many_secret_values", boxline.TooManySecretValuesError),
        (409, "secret_exists", boxline.SecretExistsError),
        (400, "secret_not_for_ai", boxline.SecretNotAllowedError),
        (400, "secret_not_for_shell", boxline.SecretNotAllowedError),
        (409, "machine_too_old", boxline.MachineTooOldError),
        (400, "variables_with_extensions", boxline.VariablesWithExtensionsError),
    ]
    for status, code, cls in cases:
        e = boxline.make_error(status, code, "m")
        assert isinstance(e, cls) and e.code == code and not e.retryable, code
    assert boxline.ErrorCode.TOO_MANY_SECRET_VALUES == "too_many_secret_values"
    f = Fake(api_error(409, "too_many_secret_values"))
    with pytest.raises(boxline.TooManySecretValuesError):
        f.sync().sessions.exec(SESSION_ID, "true", secrets=["A"])
    assert len(f.requests) == 1
