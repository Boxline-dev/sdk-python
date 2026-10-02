"""Browser settings (ad blocking, cookie banners) and extensions on a fake HTTP layer: the upload's raw body,
content type and Idempotency-Key, the new session, quick-API and agent-run fields, and the typed errors."""

from __future__ import annotations

import asyncio
import pathlib
import re
from typing import Any, List

import pytest

import boxline
from boxline import _async_client, _client
from fakes import NO_WAIT, SESSION_ID, Fake, api_error, reply, session

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

EXT = {
    "id": "ext_Q2x9L0aPz7NmK3Jb",
    "name": "Title",
    "version": "1.0",
    "description": "",
    "permissions": [],
    "hostPermissions": [],
    "sizeBytes": 4,
    "files": 1,
    "sha256": "0" * 64,
    "createdAt": "2026-09-30T00:00:00.000Z",
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    delays: List[float] = []

    async def async_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(_client, "_sleep", delays.append)
    monkeypatch.setattr(_async_client, "_sleep", async_sleep)
    return delays


def test_upload_sends_the_raw_zip_with_a_key_and_retries_with_the_same_key() -> None:
    f = Fake(api_error(503, "unavailable", NO_WAIT), reply(EXT, 201))
    zip_bytes = b"PK\x03\x04rest"
    assert f.sync().extensions.upload(zip_bytes) == EXT
    a, b = f.requests
    assert (a.method, a.url.path) == ("POST", "/v1/extensions")
    assert a.headers["content-type"] == "application/zip"
    assert a.content == zip_bytes
    assert UUID4.match(a.headers["idempotency-key"])
    assert b.headers["idempotency-key"] == a.headers["idempotency-key"]


def test_upload_from_a_path_bytearray_and_async(tmp_path: pathlib.Path) -> None:
    file = tmp_path / "ext.zip"
    file.write_bytes(b"PK\x09")
    f = Fake(reply(EXT, 201))
    bx = f.sync()
    bx.extensions.upload(str(file))
    bx.extensions.upload(file)
    bx.extensions.upload(bytearray(b"\x07\x08"))
    assert [r.content for r in f.requests] == [b"PK\x09", b"PK\x09", b"\x07\x08"]

    async def main() -> None:
        g = Fake(reply(EXT, 201))
        assert await g.async_().extensions.upload(file) == EXT
        assert g.requests[0].content == b"PK\x09"
        assert g.requests[0].headers["content-type"] == "application/zip"

    asyncio.run(main())


def test_list_get_delete() -> None:
    f = Fake(reply({"data": [EXT], "next": None}), reply(EXT), reply(None, 204))
    bx = f.sync()
    assert list(bx.extensions.list(limit=5)) == [EXT]
    assert bx.extensions.get("ext_a/b") == EXT
    assert bx.extensions.delete("ext_1") is None
    assert [f"{r.method} {r.url.raw_path.decode()}" for r in f.requests] == [
        "GET /v1/extensions?limit=5",
        "GET /v1/extensions/ext_a%2Fb",
        "DELETE /v1/extensions/ext_1",
    ]


def test_browser_settings_on_sessions_agent_runs_and_quick_apis() -> None:
    f = Fake(reply({**session(), "runId": "r", "data": {}, "pages": [], "id": "c"}, 201))
    bx = f.sync()
    bx.sessions.create(browser=True, block_ads=True, cookie_banners="off", extensions=["ext_1"])
    bx.sessions.update(SESSION_ID, block_ads=False, cookie_banners="reject")
    bx.agent.run("t", block_ads=True, cookie_banners="off", extensions=["ext_1"], allow_with_extensions=True, variables={"a": "b"})
    bx.fetch("https://example.com", block_ads=True)
    bx.screenshot("https://example.com", block_ads=True)
    bx.pdf("https://example.com", block_ads=True)
    bx.extract(url="https://example.com", prompt="p", block_ads=True)
    bx.crawl.start("https://example.com", block_ads=True)
    s = bx.sessions.get(SESSION_ID)
    s.update(block_ads=True, cookie_banners="off")
    bodies: List[Any] = [f.body(i) for i in range(len(f.requests))]
    assert {"blockAds": True, "cookieBanners": "off", "extensions": ["ext_1"]}.items() <= bodies[0].items()
    assert bodies[1] == {"blockAds": False, "cookieBanners": "reject"}
    assert {"blockAds": True, "cookieBanners": "off", "extensions": ["ext_1"], "allowWithExtensions": True}.items() <= bodies[2].items()
    for b in bodies[3:8]:
        assert b["blockAds"] is True
    assert bodies[9] == {"blockAds": True, "cookieBanners": "off"}
    # Unset, they are left out (the server's defaults apply).
    g = Fake(reply(session(), 201))
    g.sync().sessions.create()
    assert not {"blockAds", "cookieBanners", "extensions"} & set(g.body(0) or {})


@pytest.mark.parametrize(
    "status,code,cls",
    [
        (400, "variables_with_extensions", boxline.VariablesWithExtensionsError),
        (403, "extension_denied", boxline.ExtensionDeniedError),
        (403, "cross_site_request", boxline.CrossSiteRequestError),
        (400, "invalid_extension", boxline.InvalidExtensionError),
        (413, "payload_too_large", boxline.PayloadTooLargeError),
        (409, "limit_reached", boxline.LimitReachedError),
    ],
)
def test_extension_error_codes_are_typed(status: int, code: str, cls: type) -> None:
    f = Fake(api_error(status, code))
    with pytest.raises(cls) as e:
        f.sync().extensions.upload(b"\x01")
    assert (e.value.status, e.value.code, e.value.request_id, e.value.retryable) == (status, code, "req_server1", False)
    assert len(f.requests) == 1
