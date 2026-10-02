"""What the SDK does for you when things go wrong: retries with backoff (GETs, and creates that carry an
Idempotency-Key), time limits, typed errors with request ids.

    BOXLINE_API_KEY=bxl_… python examples/reliability.py
"""

import datetime
import uuid

from boxline import Boxline, BoxlineError, IdempotencyMismatchError, NotFoundError, RateLimitError

# Retries: 2 by default; timeout per request in seconds (120 by default).
bx = Boxline(max_retries=3, timeout=60)

# A create with your own Idempotency-Key: safe to send again (a crash, a timeout); it never starts a second session.
key = f"nightly-job-{datetime.date.today()}-{uuid.uuid4().hex[:6]}"
a = bx.sessions.create(timeout=120, user_metadata={"job": "nightly"}, options={"idempotency_key": key})
b = bx.sessions.create(timeout=120, user_metadata={"job": "nightly"}, options={"idempotency_key": key})
print("same key, same session:", a.id == b.id)
try:
    bx.sessions.create(timeout=300, options={"idempotency_key": key})
except IdempotencyMismatchError as e:
    print("another body with that key:", e.code)
a.release()

# Your own id for a request, to find it in your logs and ours; the API's id is error.request_id.
try:
    bx.sessions.get("00000000-0000-4000-8000-000000000000", options={"client_request_id": "my-trace-42", "max_retries": 0})
except NotFoundError as e:
    print(f"{e.status} {e.code}: {e.message} (request_id {e.request_id}, client_request_id {e.client_request_id})")

# Every error is a BoxlineError; rate limits say how long to wait.
try:
    bx.me(options={"timeout": 0.001})  # far too short, on purpose
except RateLimitError as e:
    print(f"slow down for {e.retry_after} s")
except BoxlineError as e:
    print(f"{type(e).__name__} ({e.code}), retryable: {e.retryable}")
