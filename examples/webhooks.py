"""Webhook endpoints from the API side: find a receiver's endpoint, make an event (a session that ends), wait for its
delivery and read its attempt history, send it again (the receiver drops it as a duplicate), rotate the secret (the
receiver still verifies: for 24 hours deliveries carry the old signature too) and send a test.

    python examples/webhook_receiver.py --register &     # first
    BOXLINE_API_KEY=bxl_… BOXLINE_API_URL=http://localhost:8080 python examples/webhooks.py [receiver URL]
"""

import sys
import time

from boxline import Boxline

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4901/webhooks"
bx = Boxline()

endpoint = next((w for w in bx.webhooks.list() if w["url"] == url), None)
if endpoint is None:
    raise SystemExit(f"no endpoint for {url}: start the receiver with --register first")
state = "enabled" if endpoint["enabled"] else f"off ({endpoint['disabledReason']})"
print(f"endpoint {endpoint['id']}: {state}, events {', '.join(endpoint['events'])}")

# An event: a session that ends.
session = bx.sessions.create(timeout=60, user_metadata={"from": "webhooks example"})
session.release()
print(f"session {session.id} ended ({session.end_reason})")

# Its delivery, once it has been answered.
delivery = None
for _ in range(60):
    recent = bx.webhooks.deliveries(endpoint["id"], limit=20)  # newest first: one page is enough
    delivery = next((d for d in recent.data if d["eventType"] == "session.ended" and (d.get("payload") or {}).get("data", {}).get("id") == session.id and d["status"] != "pending"), None)
    if delivery:
        break
    time.sleep(0.5)
if delivery is None:
    raise SystemExit("the session.ended delivery did not arrive within 30 s")
print(f"delivery {delivery['id']}: {delivery['status']}, HTTP {delivery['responseStatus']}, {delivery['attempts']} attempt(s), event {delivery['eventId']}")
for a in delivery["history"]:
    print(f"  attempt at {a['at']}: {a['status'] or a['errorCode']} in {a['durationMs']} ms")

# The same event again: the receiver answers "duplicate".
again = bx.webhooks.retry_delivery(endpoint["id"], delivery["id"])
print(f"sent again: {again['status']}, HTTP {again['responseStatus']}, answer \"{again['responseBody']}\"")

# A new secret; the receiver only knows the old one, which keeps signing for 24 hours.
rotated = bx.webhooks.rotate_secret(endpoint["id"])
print(f"secret rotated at {rotated['secretRotatedAt']}; the old one signs until {rotated['previousSecretExpiresAt']} (the new secret is in this response only)")

test = bx.webhooks.test(endpoint["id"])
print(f"test: {test['status']}, HTTP {test['responseStatus']} in {test['durationMs']} ms")

delivered = sum(1 for _ in bx.webhooks.deliveries(endpoint["id"], status="delivered"))
print(f"{delivered} delivered deliveries on this endpoint")
