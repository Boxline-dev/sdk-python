"""Credentials (passwords and secrets), a profile's linked credential, the ``type`` action with a credential and the
shell's env and credentials (docs/CONTRACT.md "Credentials (write-only)", "Browser profiles", "Shell environment and
credentials", "Plain-English steps", "Scripts") in both clients, on a fake HTTP layer: the requests each method sends,
that creating a credential is never retried, and the typed errors."""

from __future__ import annotations

import asyncio
import json
from typing import Any, List

import httpx
import pytest

import boxline
from fakes import NO_WAIT, SESSION_ID, Fake, api_error, reply, session

SECRET = {
    "name": "GITHUB_TOKEN",
    "type": "secret",
    "description": "CI bot",
    "origins": None,
    "shell": False,
    "scope": "shell",
    "preview": "••••1a2b",
    "createdAt": "2026-09-30T00:00:00.000Z",
    "updatedAt": "2026-09-30T00:00:00.000Z",
    "lastUsedAt": None,
}
PASSWORD = {
    "name": "SHOP",
    "type": "password",
    "description": None,
    "origins": ["https://shop.example.com"],
    "username": "ops@example.com",
    "hasTotp": True,
    "shell": False,
    "scope": "all",
    "createdAt": "2026-09-30T00:00:00.000Z",
    "updatedAt": "2026-09-30T00:00:00.000Z",
    "lastUsedAt": None,
}


def seen(f: Fake) -> List[str]:
    return [f"{r.method} {r.url.raw_path.decode()}" for r in f.requests]


def credentials_api(request: httpx.Request) -> httpx.Response:
    if request.method == "DELETE":
        return reply(None, 204)
    if request.url.path == "/v1/credentials/audit":
        return reply({"data": [{"at": "t", "action": "use", "type": "secret", "name": "GITHUB_TOKEN", "actor": None, "usedBy": {"type": "exec", "id": "s1"}, "details": {}}], "next": None})
    if request.method == "GET" and request.url.path == "/v1/credentials":
        return reply({"data": [SECRET, PASSWORD], "next": None})
    if request.method == "POST" and b'"type":"password"' in request.content:
        return reply(PASSWORD, 201)
    return reply(SECRET, 201 if request.method == "POST" else 200)


def test_credentials_methods_send_the_requests_the_api_expects() -> None:
    f = Fake(credentials_api)
    bx = f.sync()
    assert bx.credentials.create("GITHUB_TOKEN", "secret", value="v-123", description="CI bot", scope="shell") == SECRET
    shop = bx.credentials.create(
        "SHOP", "password", origins=["https://shop.example.com"], username="ops@example.com", password="pw-1", totp_secret="JBSWY3DPEHPK3PXP", scope="all"
    )
    assert shop["username"] == "ops@example.com" and shop["hasTotp"] is True
    listed = bx.credentials.list(limit=10).data
    assert [c["type"] for c in listed] == ["secret", "password"]
    bx.credentials.get("GITHUB/TOKEN")
    bx.credentials.update("GITHUB_TOKEN", value="v-456", description=None, origins=None, shell=True, scope="all")
    bx.credentials.update("SHOP", password="pw-2", totp_secret=None, origins=["https://shop.example.com", "https://*.example.org"])
    bx.credentials.update("SHOP", scope="agent")
    entry = bx.credentials.audit(name="GITHUB_TOKEN", limit=5).data[0]
    assert entry["usedBy"] == {"type": "exec", "id": "s1"} and entry["type"] == "secret"
    bx.credentials.delete("GITHUB_TOKEN")
    assert seen(f) == [
        "POST /v1/credentials",
        "POST /v1/credentials",
        "GET /v1/credentials?limit=10",
        "GET /v1/credentials/GITHUB%2FTOKEN",
        "PATCH /v1/credentials/GITHUB_TOKEN",
        "PATCH /v1/credentials/SHOP",
        "PATCH /v1/credentials/SHOP",
        "GET /v1/credentials/audit?name=GITHUB_TOKEN&limit=5",
        "DELETE /v1/credentials/GITHUB_TOKEN",
    ]
    assert f.body(0) == {"name": "GITHUB_TOKEN", "type": "secret", "value": "v-123", "description": "CI bot", "scope": "shell"}
    assert f.body(1) == {
        "name": "SHOP",
        "type": "password",
        "origins": ["https://shop.example.com"],
        "username": "ops@example.com",
        "password": "pw-1",
        "totpSecret": "JBSWY3DPEHPK3PXP",
        "scope": "all",
    }
    # None clears the description and allows any site: it reaches the API as null; fields not passed are left out.
    assert f.body(4) == {"value": "v-456", "description": None, "origins": None, "shell": True, "scope": "all"}
    # totp_secret=None removes 2FA.
    assert f.body(5) == {"password": "pw-2", "totpSecret": None, "origins": ["https://shop.example.com", "https://*.example.org"]}
    assert f.body(6) == {"scope": "agent"}
    assert all("idempotency-key" not in r.headers for r in f.requests)
    # No value is ever in a URL.
    assert not any(v in r.url.raw_path.decode() for r in f.requests for v in ("v-123", "v-456", "pw-1", "pw-2", "JBSWY3"))


def test_async_credentials_and_pages() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        return reply({"data": [{**SECRET, "name": "B" if after else "A"}], "next": None if after else "c2"})

    async def go() -> List[str]:
        abx = Fake(answer).async_()
        return [c["name"] async for c in abx.credentials.list(limit=1)]

    assert asyncio.run(go()) == ["A", "B"]
    f = Fake(credentials_api)
    assert asyncio.run(f.async_().credentials.get("GITHUB_TOKEN")) == SECRET
    g = Fake(credentials_api)
    made = asyncio.run(g.async_().credentials.create("SHOP", "password", origins=["https://shop.example.com"], username="ops@example.com", password="pw-1"))
    assert made == PASSWORD and g.body(0)["type"] == "password"


def test_creating_a_credential_is_never_retried_and_its_errors_are_typed() -> None:
    f = Fake(api_error(503, "unavailable", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        f.sync().credentials.create("A_TOKEN", "secret", value="x-1")
    assert len(f.requests) == 1
    with pytest.raises(boxline.CredentialExistsError):
        Fake(api_error(409, "credential_exists")).sync().credentials.create("A_TOKEN", "secret", value="x-1")
    with pytest.raises(boxline.PlanLimitError):
        Fake(api_error(402, "plan_limit")).sync().credentials.create("A_TOKEN", "secret", value="x-1")
    with pytest.raises(boxline.FeatureNotInPlanError):
        Fake(api_error(402, "feature_not_in_plan")).sync().credentials.create("SHOP", "password", origins=["https://a.example.com"], username="u", password="p")
    with pytest.raises(boxline.NotFoundError):
        Fake(api_error(404, "credential_not_found")).sync().credentials.get("NOPE")


def test_a_profile_links_a_credential() -> None:
    profile = {"id": "prof_1", "name": "shop", "sizeBytes": 0, "createdAt": "t", "updatedAt": "t", "inUseBy": None, "credential": "SHOP"}
    f = Fake(lambda r: reply({**profile, "credential": json.loads(r.content).get("credential")}))
    bx = f.sync()
    assert bx.profiles.update("prof_1", credential="SHOP")["credential"] == "SHOP"
    assert bx.profiles.update("prof_1", credential=None)["credential"] is None
    bx.profiles.update("prof_1", name="Shop sign-in")
    assert seen(f) == ["PATCH /v1/profiles/prof_1"] * 3
    assert [f.body(i) for i in range(3)] == [{"credential": "SHOP"}, {"credential": None}, {"name": "Shop sign-in"}]
    # The old login-detail and rename methods, and bx.secrets, are gone.
    for old in ("rename", "set_login", "update_login", "delete_login"):
        assert not hasattr(bx.profiles, old), old
    assert not hasattr(bx, "secrets")
    with pytest.raises(boxline.NotFoundError):
        Fake(api_error(404, "credential_not_found")).sync().profiles.update("prof_1", credential="NOPE")
    with pytest.raises(boxline.FeatureNotInPlanError):
        Fake(api_error(402, "feature_not_in_plan")).sync().profiles.update("prof_1", credential="SHOP")


def test_the_type_action_with_a_credential() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        actions = json.loads(request.content)["actions"]
        return reply({"results": [{"ok": True, "action": a["action"], "text": "Typed %SHOP.password% into #pw", "ms": 1} for a in actions]})

    f = Fake(answer)
    s = boxline.Session(f.sync(), session())
    s.type_credential("SHOP", "password", "#pw")
    s.type_credential("GITHUB_TOKEN")
    s.type_credential("SHOP", field="otp", selector="#code", allow_with_extensions=True)
    assert [f.body(i) for i in range(3)] == [
        {"actions": [{"action": "type", "credential": "SHOP", "field": "password", "selector": "#pw"}]},
        {"actions": [{"action": "type", "credential": "GITHUB_TOKEN"}]},
        {"actions": [{"action": "type", "credential": "SHOP", "field": "otp", "selector": "#code", "allowWithExtensions": True}]},
    ]

    async def typed() -> None:
        await boxline.AsyncSession(f.async_(), session()).type_credential("SHOP", "username", "#user")

    asyncio.run(typed())
    assert f.body(3) == {"actions": [{"action": "type", "credential": "SHOP", "field": "username", "selector": "#user"}]}


def test_a_refused_type_action_raises_the_error_its_code_names() -> None:
    def refuse(code: str) -> Any:
        f = Fake(reply({"results": [{"ok": False, "action": "type", "error": "refused", "code": code, "ms": 1}]}))
        return boxline.Session(f.sync(), session()).type_credential("SHOP", "password", "#pw")

    with pytest.raises(boxline.NotFoundError) as missing:
        refuse("credential_not_found")
    assert missing.value.status == 404
    with pytest.raises(boxline.CredentialNotAllowedError):
        refuse("credential_not_for_ai")
    with pytest.raises(boxline.FeatureNotInPlanError) as plan:
        refuse("feature_not_in_plan")
    assert plan.value.status == 402
    with pytest.raises(boxline.VariablesWithExtensionsError):
        refuse("variables_with_extensions")


def test_env_and_credentials_on_sessions_and_exec() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/exec"):
            if b'"stream":true' in request.content:
                return reply(content=b'{"type":"stdout","data":"%API_KEY%\\n"}\n{"type":"exit","exitCode":0}\n')
            return reply({"stdout": "%API_KEY%\n", "stderr": "", "exitCode": 0, "timedOut": False, "durationMs": 1})
        return reply(session(env=["REGION"], credentials=["GITHUB_TOKEN", "SHOP"]), 201)

    f = Fake(answer)
    bx = f.sync()
    s = bx.sessions.create(shell=True, env={"REGION": "eu", "DEBUG": True}, credentials=["GITHUB_TOKEN", "SHOP"])
    assert s.data["env"] == ["REGION"] and s.data["credentials"] == ["GITHUB_TOKEN", "SHOP"]
    s.exec("echo $API_KEY", env={"MODE": "x"}, credentials=["API_KEY"])
    bx.sessions.exec(SESSION_ID, "echo $API_KEY", credentials=["API_KEY"])
    with s.exec_stream("echo $API_KEY", credentials=["API_KEY"]) as proc:
        assert [text for _, text in proc] == ["%API_KEY%\n"]
    assert f.body(0) == {"browser": True, "shell": True, "env": {"REGION": "eu", "DEBUG": True}, "credentials": ["GITHUB_TOKEN", "SHOP"]}
    assert f.requests[0].headers.get("idempotency-key")
    assert f.body(1) == {"command": "echo $API_KEY", "env": {"MODE": "x"}, "credentials": ["API_KEY"]}
    assert f.body(2) == {"command": "echo $API_KEY", "credentials": ["API_KEY"]}
    assert f.body(3) == {"command": "echo $API_KEY", "credentials": ["API_KEY"], "stream": True}


def test_credentials_on_steps_scripts_agent_runs_and_tasks() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/scripts/run"):
            return reply(content=b'{"type":"exit","exitCode":0}\n')
        if p.endswith("/actions"):
            return reply({"results": [{"ok": True, "action": "step", "value": {"method": "fill"}, "ms": 1}]})
        if p.startswith("/v1/tasks"):
            return reply({"id": "task_1", "name": "n", "instruction": "i", "variables": [], "credentials": ["SHOP"], "profile": None}, 201 if request.method == "POST" else 200)
        if p == "/v1/agent/runs":
            return reply({"id": "run_1", "status": "running", "sessionId": "s1", "provider": "openai", "model": "m", "mode": "tools"}, 201)
        return reply(session())

    for flavour in ("sync", "async"):
        f = Fake(answer)

        async def calls(bx: Any, aw: Any) -> None:
            s = await aw(bx.sessions.get(SESSION_ID))
            await aw(s.step("type %SHOP.password% into the password field", credentials=["SHOP"], allow_with_extensions=True))
            proc = await aw(s.run_script("await step('sign in')", credentials=["SHOP"], allow_with_extensions=True))
            await aw(proc.wait())
            await aw(bx.agent.run("sign in", credentials=["SHOP", "GITHUB_TOKEN"], profile="prof_1", allow_with_extensions=True))
            await aw(bx.agent.run("sign in", profile={"id": "prof_1", "persist": True}))
            await aw(bx.tasks.create("n", "i", credentials=("SHOP",)))
            await aw(bx.tasks.update("task_1", credentials=None))

        async def plain(v: Any) -> Any:
            return v

        async def awaited(v: Any) -> Any:
            return await v

        asyncio.run(calls(f.sync(), plain) if flavour == "sync" else calls(f.async_(), awaited))
        assert f.body(1) == {"actions": [{"action": "step", "instruction": "type %SHOP.password% into the password field", "credentials": ["SHOP"], "allowWithExtensions": True}]}, flavour
        assert f.body(2) == {"code": "await step('sign in')", "credentials": ["SHOP"], "allowWithExtensions": True}
        assert f.body(3) == {"task": "sign in", "credentials": ["SHOP", "GITHUB_TOKEN"], "profile": {"id": "prof_1"}, "allowWithExtensions": True}
        assert f.body(4) == {"task": "sign in", "profile": {"id": "prof_1", "persist": True}}
        assert f.body(5) == {"name": "n", "instruction": "i", "credentials": ["SHOP"]}
        assert f.body(6) == {"credentials": None}


def test_move_returns_where_the_shell_continues() -> None:
    shell = {"cwd": "/workspace/project", "exported": ["NODE_ENV"], "stoppedProcesses": [{"pid": 42, "command": "npm run dev", "seconds": 90}]}
    timings = {"captureMs": 1, "acquireMs": 2, "restoreMs": 3, "totalMs": 6}
    assert Fake(reply({"session": session(), "timings": timings, "shell": shell})).sync().sessions.move(SESSION_ID)["shell"] == shell
    assert Fake(reply({"session": session(), "timings": timings})).sync().sessions.move(SESSION_ID)["shell"] is None


def test_credential_error_codes() -> None:
    cases = [
        (409, "too_many_credential_values", boxline.TooManyCredentialValuesError),
        (409, "credential_exists", boxline.CredentialExistsError),
        (400, "credential_not_for_ai", boxline.CredentialNotAllowedError),
        (400, "credential_not_for_shell", boxline.CredentialNotAllowedError),
        (404, "credential_not_found", boxline.NotFoundError),
        (409, "machine_too_old", boxline.MachineTooOldError),
        (400, "variables_with_extensions", boxline.VariablesWithExtensionsError),
    ]
    for status, code, cls in cases:
        e = boxline.make_error(status, code, "m")
        assert isinstance(e, cls) and e.code == code and not e.retryable, code
    assert boxline.ErrorCode.TOO_MANY_CREDENTIAL_VALUES == "too_many_credential_values"
    assert boxline.ErrorCode.CREDENTIAL_NOT_FOUND == "credential_not_found"
    f = Fake(api_error(409, "too_many_credential_values"))
    with pytest.raises(boxline.TooManyCredentialValuesError):
        f.sync().sessions.exec(SESSION_ID, "true", credentials=["A"])
    assert len(f.requests) == 1
