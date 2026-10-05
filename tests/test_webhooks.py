"""verify_webhook must accept and refuse exactly what the API's reference signer does (apps/api/src/webhooks/signing.ts).
webhook_vectors.json is made by that signer (tests/helpers/webhook-vectors.ts in the platform repository); the Node
SDK's test checks the same file, and fails when it is stale."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

import boxline
from boxline import WebhookSignatureError, verify_webhook, webhook_signature_header
from fakes import Fake, reply

VECTORS = json.loads((Path(__file__).parent / "webhook_vectors.json").read_text(encoding="utf-8"))


def outcome(fn: Callable[[], Any]) -> str:
    try:
        fn()
        return "ok"
    except WebhookSignatureError as e:
        return e.reason
    except ValueError:  # json: a genuine body that is not JSON; the signature was accepted
        return "ok"


def body_of(v: Dict[str, Any]) -> Any:
    return bytes.fromhex(v["bodyHex"]) if v["body"] is None else v["body"]


def test_the_vectors_cover_every_outcome() -> None:
    assert len(VECTORS) > 40
    assert {v["expect"] for v in VECTORS} == {"ok", "malformed", "timestamp_out_of_range", "no_matching_signature"}


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(v: Dict[str, Any]) -> None:
    kw: Dict[str, Any] = {"now": v["now"] / 1000}
    if "toleranceSeconds" in v:
        kw["tolerance_seconds"] = v["toleranceSeconds"]
    secret = v["secrets"][0] if len(v["secrets"]) == 1 else v["secrets"]
    body = body_of(v)
    assert outcome(lambda: verify_webhook(body, v["header"], secret, **kw)) == v["expect"]
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    for form in (raw, bytearray(raw), memoryview(raw)):
        assert outcome(lambda: verify_webhook(form, v["header"], secret, **kw)) == v["expect"]
    if v.get("signedWith") and v["expect"] != "no_matching_signature":
        assert webhook_signature_header(v["signedWith"]["secrets"], body, v["signedWith"]["timestamp"]) == v["header"]
    if v["expect"] == "ok" and v["body"]:
        try:
            parsed = json.loads(v["body"])
        except ValueError:
            return
        assert verify_webhook(body, v["header"], secret, **kw) == parsed


def test_error_details() -> None:
    body = json.dumps({"id": "evt_1"})
    with pytest.raises(WebhookSignatureError) as e:
        verify_webhook(body, None, "whsec_a")
    assert (e.value.reason, e.value.status, e.value.retryable) == ("malformed", 400, False)
    assert str(e.value) == "the Boxline-Signature header is missing or malformed"
    assert isinstance(e.value, boxline.BoxlineError)
    with pytest.raises(WebhookSignatureError) as e2:
        verify_webhook(body, webhook_signature_header("whsec_a", body, 1000), "whsec_a", now=2000)
    assert str(e2.value) == "the Boxline-Signature timestamp is more than 5 minutes from now"
    with pytest.raises(WebhookSignatureError) as e3:
        verify_webhook(body, webhook_signature_header("whsec_a", body), "whsec_b")
    assert str(e3.value) == "no signature matches this secret (check the secret, and that the body is the raw bytes as received)"


def test_returns_the_event() -> None:
    body = json.dumps({"id": "evt_7", "type": "session.stopped", "createdAt": "t", "projectId": "p", "data": {"status": "STOPPED"}}).encode()
    event = verify_webhook(body, webhook_signature_header("whsec_x", body), ["whsec_old", "whsec_x"])
    assert event["id"] == "evt_7" and event["data"]["status"] == "STOPPED"


def test_webhooks_and_settings_requests() -> None:
    f = Fake(reply({"id": "wh_1", "secret": "whsec_new", "data": [], "next": None, "captchaDefault": "solve"}))
    bx = f.sync()
    assert bx.webhooks.create("https://example.com/hook", ["session.stopped"], description="ci")["secret"] == "whsec_new"
    bx.webhooks.list(limit=10)
    bx.webhooks.get("wh_1")
    bx.webhooks.update("wh_1", enabled=False)
    bx.webhooks.update("wh_1", description=None)
    bx.webhooks.delete("wh_1")
    bx.webhooks.rotate_secret("wh_1")
    bx.webhooks.test("wh_1")
    bx.webhooks.deliveries("wh_1", status="failed", limit=5)
    bx.webhooks.retry_delivery("wh_1", "dlv_1")
    assert bx.project.settings()["captchaDefault"] == "solve"
    bx.project.set_settings(captcha_default="ask")
    assert [f"{r.method} {r.url.path}{('?' + r.url.query.decode()) if r.url.query else ''}" for r in f.requests] == [
        "POST /v1/webhooks",
        "GET /v1/webhooks?limit=10",
        "GET /v1/webhooks/wh_1",
        "PATCH /v1/webhooks/wh_1",
        "PATCH /v1/webhooks/wh_1",
        "DELETE /v1/webhooks/wh_1",
        "POST /v1/webhooks/wh_1/rotate-secret",
        "POST /v1/webhooks/wh_1/test",
        "GET /v1/webhooks/wh_1/deliveries?status=failed&limit=5",
        "POST /v1/webhooks/wh_1/deliveries/dlv_1/retry",
        "GET /v1/project/settings",
        "PUT /v1/project/settings",
    ]
    assert f.body(0) == {"url": "https://example.com/hook", "events": ["session.stopped"], "description": "ci"}
    assert f.body(3) == {"enabled": False}
    assert f.body(4) == {"description": None}
    assert f.body(11) == {"captchaDefault": "ask"}


def test_event_types_samples_and_all_events() -> None:
    f = Fake(reply({"id": "wh_1", "secret": "whsec_new", "data": [{"type": "session.started", "group": "sessions", "description": "d"}], "all": "*"}))
    bx = f.sync()
    assert bx.webhooks.event_types()["all"] == "*"
    bx.webhooks.test("wh_1", type="agent_run.waiting")
    bx.webhooks.test("wh_1")
    bx.webhooks.create("https://example.com/hook", ["*"])
    assert [f"{r.method} {r.url.path}" for r in f.requests] == ["GET /v1/webhooks/events", "POST /v1/webhooks/wh_1/test", "POST /v1/webhooks/wh_1/test", "POST /v1/webhooks"]
    assert f.body(1) == {"type": "agent_run.waiting"}
    assert f.body(2) is None  # no type: a webhook.test event, as before
    assert f.body(3) == {"url": "https://example.com/hook", "events": ["*"]}


def test_typed_payloads_cover_every_type() -> None:
    import typing

    from boxline import types as t

    subscribable = set(typing.get_args(t.WebhookEventType))
    keyed = {typing.get_args(c.__annotations__["type"])[0] for c in typing.get_args(t.WebhookEventPayload)}
    assert keyed == subscribable | {"webhook.test"}
    assert len(subscribable) == 24


def test_webhook_error_classes() -> None:
    assert isinstance(boxline.make_error(400, "webhook_url_not_allowed", "m"), boxline.WebhookUrlNotAllowedError)
    unavailable = boxline.make_error(503, "webhooks_unavailable", "m")
    assert isinstance(unavailable, boxline.WebhooksUnavailableError) and not unavailable.retryable
    assert isinstance(boxline.make_error(409, "webhook_disabled", "m"), boxline.WebhookDisabledError)
    assert isinstance(boxline.make_error(409, "payload_expired", "m"), boxline.PayloadExpiredError)
    assert boxline.ErrorCode.QUEUE_FULL == "queue_full"
