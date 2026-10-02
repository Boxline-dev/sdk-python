"""Verifying webhook deliveries (docs/CONTRACT.md "Webhooks", "Signatures"). Every delivery carries

    Boxline-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">[,v1=…]

keyed with the endpoint's secret (the whole ``whsec_…`` string as UTF-8); for 24 hours after a rotation a second v1
signed with the old secret follows. ``verify_webhook`` accepts and refuses exactly what the API's reference
(apps/api/src/webhooks/signing.ts, JavaScript) does, down to how JavaScript trims white space, what it counts as a
digit and how it measures the header's length; tests/test_webhooks.py checks it against vectors from that signer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from typing import Any, List, Optional, Sequence, Tuple, Union

from ._errors import WebhookSignatureError

SIGNATURE_HEADER = "Boxline-Signature"
#: How far a delivery's timestamp may be from the receiver's clock (seconds).
TOLERANCE_SECONDS = 300

# What JavaScript's String.prototype.trim removes: WhiteSpace (tab, VT, FF, space, NBSP, BOM and the Zs characters)
# and LineTerminator. Python's own str.strip() differs (it removes \x1c-\x1f and \x85, and keeps the BOM).
_JS_WHITESPACE = "\t\n\x0b\x0c\r \xa0                　﻿"
_T = re.compile(r"[0-9]{1,12}")  # JavaScript's \d is ASCII only
_V1 = re.compile(r"[0-9a-f]{64}")

Body = Union[bytes, bytearray, memoryview, str]


def _utf8(s: str) -> bytes:
    """UTF-8 as JavaScript encodes it: a lone surrogate becomes U+FFFD."""
    try:
        return s.encode("utf-8")
    except UnicodeEncodeError:
        return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace").encode("utf-8")


def _utf16_length(s: str) -> int:
    """The length JavaScript reports: characters beyond U+FFFF count twice."""
    return len(s) + sum(1 for c in s if ord(c) > 0xFFFF)


def _parse(header: Any) -> Optional[Tuple[int, List[str]]]:
    """Reads ``t=…,v1=…[,v1=…]`` exactly as the API does; other keys are ignored. None when there is no usable t or v1."""
    if isinstance(header, (bytes, bytearray)):
        header = bytes(header).decode("latin-1")  # HTTP header octets
    if not isinstance(header, str) or _utf16_length(header) > 4096:
        return None
    timestamp: Optional[int] = None
    v1: List[str] = []
    for part in header.split(","):
        i = part.find("=")
        if i <= 0:
            continue
        key = part[:i].strip(_JS_WHITESPACE)
        value = part[i + 1 :].strip(_JS_WHITESPACE)
        if key == "t" and _T.fullmatch(value):
            timestamp = int(value)
        elif key == "v1" and _V1.fullmatch(value):
            v1.append(value)
    return (timestamp, v1) if timestamp is not None and v1 else None


def _body_bytes(body: Body) -> bytes:
    return _utf8(body) if isinstance(body, str) else bytes(body)


def _signature(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(_utf8(secret), f"{timestamp}.".encode("ascii") + body, hashlib.sha256).hexdigest()


def _failure(reason: str, tolerance: float, several: bool) -> WebhookSignatureError:
    if reason == "malformed":
        message = f"the {SIGNATURE_HEADER} header is missing or malformed"
    elif reason == "timestamp_out_of_range":
        span = f"{int(tolerance // 60)} minutes" if tolerance % 60 == 0 else f"{tolerance:g} seconds"
        message = f"the {SIGNATURE_HEADER} timestamp is more than {span} from now"
    else:
        message = f"no signature matches {'these secrets' if several else 'this secret'} (check the secret, and that the body is the raw bytes as received)"
    return WebhookSignatureError(reason, message)


def verify_webhook(
    body: Body,
    header: Optional[Union[str, bytes]],
    secret: Union[str, Sequence[str]],
    tolerance_seconds: float = TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> Any:
    """Checks a webhook delivery and returns its event (the parsed body); raises WebhookSignatureError otherwise.

    - ``body``: the raw request body exactly as received (bytes; a str is taken as UTF-8). Re-serialised JSON will not
      match.
    - ``header``: the ``Boxline-Signature`` header.
    - ``secret``: the endpoint's ``whsec_…`` secret, or several (e.g. ``[new_secret, old_secret]`` while you switch
      after a rotation): the delivery is accepted when any of them signed it.
    - ``now``: the time in seconds (default ``time.time()``), for tests.

    A delivery can arrive more than once: after verifying, drop an event ``id`` you have handled already.
    """
    secrets = [secret] if isinstance(secret, str) else list(secret)
    parsed = _parse(header)
    if parsed is None:
        raise _failure("malformed", tolerance_seconds, len(secrets) > 1)
    timestamp, v1 = parsed
    current = math.floor(time.time() if now is None else now)
    if abs(current - timestamp) > tolerance_seconds:
        raise _failure("timestamp_out_of_range", tolerance_seconds, len(secrets) > 1)
    raw = _body_bytes(body)
    match = False
    for s in secrets:
        want = _signature(s, timestamp, raw)
        # Every signature is compared, so the time taken does not say which one (or how much of it) matched.
        for got in v1:
            if hmac.compare_digest(got, want):
                match = True
    if not match:
        raise _failure("no_matching_signature", tolerance_seconds, len(secrets) > 1)
    # Decoded like the API's reference: invalid UTF-8 becomes U+FFFD and a leading byte-order mark stays.
    return json.loads(body if isinstance(body, str) else raw.decode("utf-8", "replace"))


def webhook_signature_header(secret: Union[str, Sequence[str]], body: Body, timestamp: Optional[int] = None) -> str:
    """A ``Boxline-Signature`` value for ``body``, as the API signs deliveries: for testing your own receiver. With
    several secrets it has one v1 per secret, the first one first (as during a rotation)."""
    secrets = [secret] if isinstance(secret, str) else list(secret)
    if not secrets:
        raise ValueError("no signing secret")
    t = int(time.time()) if timestamp is None else timestamp
    raw = _body_bytes(body)
    return ",".join([f"t={t}", *(f"v1={_signature(s, t, raw)}" for s in secrets)])
