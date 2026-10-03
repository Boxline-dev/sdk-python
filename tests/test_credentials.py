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
    "codeSource": "totp",
    "codeUrl": None,
    "codeTimeoutSeconds": 300,
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
        (408, "credential_code_timeout", boxline.CredentialCodeTimeoutError),
        (400, "credential_link_wrong_site", boxline.CredentialLinkWrongSiteError),
        (422, "credential_login_failed", boxline.CredentialLoginFailedError),
        (408, "credential_login_timeout", boxline.CredentialLoginTimeoutError),
        (400, "code_url_not_allowed", boxline.CodeUrlNotAllowedError),
    ]
    for status, code, cls in cases:
        e = boxline.make_error(status, code, "m")
        assert isinstance(e, cls) and e.code == code and not e.retryable, code
    assert boxline.ErrorCode.TOO_MANY_CREDENTIAL_VALUES == "too_many_credential_values"
    assert boxline.ErrorCode.CREDENTIAL_NOT_FOUND == "credential_not_found"
    assert boxline.ErrorCode.CREDENTIAL_CODE_TIMEOUT == "credential_code_timeout"
    assert boxline.ErrorCode.CREDENTIAL_LINK_WRONG_SITE == "credential_link_wrong_site"
    assert boxline.ErrorCode.CREDENTIAL_LOGIN_FAILED == "credential_login_failed"
    assert boxline.ErrorCode.CODE_URL_NOT_ALLOWED == "code_url_not_allowed"
    f = Fake(api_error(409, "too_many_credential_values"))
    with pytest.raises(boxline.TooManyCredentialValuesError):
        f.sync().sessions.exec(SESSION_ID, "true", credentials=["A"])
    assert len(f.requests) == 1


CODE = "482913"
LINK = "https://shop.example.com/magic?token=abc123"
URL_PASSWORD = {**PASSWORD, "hasTotp": False, "codeSource": "url", "codeUrl": "https://ops.example.com/boxline-codes"}


def test_create_and_update_send_the_code_source_and_the_code_url_secret_comes_back_once() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.method == "POST":
            return reply({**URL_PASSWORD, "codeUrlSecret": "whsec_once"} if body.get("codeSource") == "url" else {**PASSWORD, "hasTotp": False, "codeSource": "push"}, 201)
        return reply({**URL_PASSWORD, "codeUrlSecret": "whsec_twice"})

    f = Fake(answer)
    bx = f.sync()
    push = bx.credentials.create("SHOP", "password", origins=["https://shop.example.com"], username="u", password="pw-1", code_source="push", code_timeout_seconds=120)
    assert push["codeSource"] == "push" and "codeUrlSecret" not in push
    url = bx.credentials.create(
        "SHOP2", "password", origins=["https://shop.example.com"], username="u", password="pw-1", code_source="url", code_url=URL_PASSWORD["codeUrl"]
    )
    assert url["codeUrl"] == URL_PASSWORD["codeUrl"] and url["codeUrlSecret"] == "whsec_once"
    assert bx.credentials.update("SHOP2", password="pw-1", code_url="https://ops.example.com/new")["codeUrlSecret"] == "whsec_twice"
    bx.credentials.update("SHOP2", password="pw-1", code_source=None)
    bx.credentials.update("SHOP2", code_timeout_seconds=60)
    assert [f.body(i) for i in range(5)] == [
        {"name": "SHOP", "type": "password", "origins": ["https://shop.example.com"], "username": "u", "password": "pw-1", "codeSource": "push", "codeTimeoutSeconds": 120},
        {"name": "SHOP2", "type": "password", "origins": ["https://shop.example.com"], "username": "u", "password": "pw-1", "codeSource": "url", "codeUrl": URL_PASSWORD["codeUrl"]},
        {"password": "pw-1", "codeUrl": "https://ops.example.com/new"},
        # code_source=None removes 2FA: it reaches the API as null; a field not passed is left out.
        {"password": "pw-1", "codeSource": None},
        {"codeTimeoutSeconds": 60},
    ]


def test_push_code_sends_a_code_or_a_link_never_in_the_url_and_is_not_retried() -> None:
    f = Fake(reply({"accepted": True, "kind": "code", "expiresAt": "2026-10-03T12:10:00.000Z"}, 202))
    bx = f.sync()
    assert bx.credentials.push_code("SHOP", code=CODE)["accepted"] is True
    bx.credentials.push_code("SHOP", link=LINK)
    assert seen(f) == ["POST /v1/credentials/SHOP/codes", "POST /v1/credentials/SHOP/codes"]
    assert [f.body(0), f.body(1)] == [{"code": CODE}, {"link": LINK}]
    assert all("idempotency-key" not in r.headers for r in f.requests)
    assert not any(CODE in r.url.raw_path.decode() for r in f.requests)
    # Exactly one of code and link.
    with pytest.raises(ValueError):
        bx.credentials.push_code("SHOP")
    with pytest.raises(ValueError):
        bx.credentials.push_code("SHOP", code=CODE, link=LINK)
    assert len(f.requests) == 2

    down = Fake(api_error(503, "unavailable", NO_WAIT))
    with pytest.raises(boxline.BoxlineError):
        down.sync().credentials.push_code("SHOP", code=CODE)
    assert len(down.requests) == 1
    with pytest.raises(boxline.CredentialLinkWrongSiteError):
        Fake(api_error(400, "credential_link_wrong_site")).sync().credentials.push_code("SHOP", link="https://evil.example.net/x")
    with pytest.raises(boxline.NotFoundError):
        Fake(api_error(404, "credential_not_found")).sync().credentials.push_code("NOPE", code=CODE)


def test_rotate_code_url_secret_and_a_refused_code_url() -> None:
    f = Fake(reply({"codeUrlSecret": "whsec_new"}))
    assert f.sync().credentials.rotate_code_url_secret("SHOP") == {"codeUrlSecret": "whsec_new"}
    assert seen(f) == ["POST /v1/credentials/SHOP/code-url-secret"]
    with pytest.raises(boxline.CodeUrlNotAllowedError) as e:
        Fake(api_error(400, "code_url_not_allowed")).sync().credentials.create(
            "SHOP", "password", origins=["https://shop.example.com"], username="u", password="p", code_source="url", code_url="https://127.0.0.1/x"
        )
    assert e.value.status == 400


def test_async_push_code_and_rotate() -> None:
    f = Fake(reply({"accepted": True, "kind": "link", "expiresAt": "t"}, 202))

    async def go() -> Any:
        abx = f.async_()
        await abx.credentials.push_code("SHOP", link=LINK)
        return await abx.credentials.rotate_code_url_secret("SHOP")

    asyncio.run(go())
    assert seen(f) == ["POST /v1/credentials/SHOP/codes", "POST /v1/credentials/SHOP/code-url-secret"]
    assert f.body(0) == {"link": LINK}


def test_session_login_sends_the_login_action_and_failures_raise_their_codes() -> None:
    value = {"url": "https://shop.example.com/account", "title": "Your account", "runId": "run_1"}
    f = Fake(reply({"results": [{"ok": True, "action": "login", "value": value, "text": "Signed in with SHOP", "ms": 9}]}))
    s = boxline.Session(f.sync(), session())
    assert s.login("SHOP", url="https://shop.example.com/login") == value
    s.login()
    s.login("SHOP", allow_with_extensions=True)
    assert [f.body(i) for i in range(3)] == [
        {"actions": [{"action": "login", "credential": "SHOP", "url": "https://shop.example.com/login"}]},
        {"actions": [{"action": "login"}]},
        {"actions": [{"action": "login", "credential": "SHOP", "allowWithExtensions": True}]},
    ]

    async def go() -> Any:
        return await boxline.AsyncSession(f.async_(), session()).login("SHOP")

    assert asyncio.run(go()) == value

    def failing(code: str, **extra: Any) -> Any:
        g = Fake(reply({"results": [{"ok": False, "action": "login", "error": "could not sign in with SHOP: the page said no", "code": code, "ms": 1, **extra}]}))
        return boxline.Session(g.sync(), session()).login("SHOP")

    with pytest.raises(boxline.CredentialLoginFailedError) as failed:
        failing("credential_login_failed", runId="run_9")
    assert failed.value.run_id == "run_9" and "could not sign in" in str(failed.value)
    # A login that ran out of time and was canceled is a kind of failed login: it has the run too.
    with pytest.raises(boxline.CredentialLoginTimeoutError) as too_slow:
        failing("credential_login_timeout", runId="run_9")
    assert isinstance(too_slow.value, boxline.CredentialLoginFailedError)
    assert too_slow.value.status == 408 and too_slow.value.run_id == "run_9"
    with pytest.raises(boxline.CredentialCodeTimeoutError) as timeout:
        failing("credential_code_timeout", runId="run_9")
    assert timeout.value.status == 408
    with pytest.raises(boxline.CredentialLinkWrongSiteError):
        failing("credential_link_wrong_site")
    with pytest.raises(boxline.FeatureNotInPlanError):
        failing("feature_not_in_plan")


def test_type_credential_otp_that_never_comes_raises_the_timeout_error() -> None:
    f = Fake(reply({"results": [{"ok": False, "action": "type", "error": "no code arrived", "code": "credential_code_timeout", "ms": 1}]}))
    with pytest.raises(boxline.CredentialCodeTimeoutError):
        boxline.Session(f.sync(), session()).type_credential("SHOP", "otp", "#code")
