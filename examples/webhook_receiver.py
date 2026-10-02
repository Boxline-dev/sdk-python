"""A webhook receiver (Python's standard library only): it verifies every delivery's signature against the raw body,
drops events it has handled already (by the id inside the signed body: a delivery can arrive more than once), and
answers 2xx at once.

    BOXLINE_WEBHOOK_SECRET=whsec_… python examples/webhook_receiver.py
        The endpoint's secret; while you switch after a rotation, both: "whsec_new,whsec_old".

    BOXLINE_API_KEY=bxl_… BOXLINE_API_URL=http://localhost:8080 python examples/webhook_receiver.py --register
        Local development: registers http://127.0.0.1:4901/webhooks as an endpoint (the API needs WEBHOOKS_ALLOW_LOCAL=1;
        real endpoints are public HTTPS), keeps its secret in memory only, and deletes the endpoint on Ctrl-C.
"""

import os
import signal
import sys
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from boxline import WEBHOOK_SIGNATURE_HEADER, Boxline, WebhookSignatureError, verify_webhook

PORT = int(os.environ.get("PORT", "4901"))
URL = f"http://127.0.0.1:{PORT}/webhooks"
secrets = [s for s in os.environ.get("BOXLINE_WEBHOOK_SECRET", "").split(",") if s]
registered = None

if "--register" in sys.argv:
    bx = Boxline()
    endpoint = bx.webhooks.create(URL, ["session.ended", "agent_run.finished", "crawl.finished", "captcha.waiting", "webhook.disabled"], description="webhook_receiver example")
    secrets = [endpoint["secret"]]  # shown only in this response: kept in memory, never printed
    registered = (bx, endpoint["id"])
    print(f"[receiver] registered endpoint {endpoint['id']} for {URL} ({', '.join(endpoint['events'])})", flush=True)
if not secrets:
    raise SystemExit("set BOXLINE_WEBHOOK_SECRET, or pass --register")

# Event ids already handled. A real receiver keeps them in its database, next to what it did with the event.
handled: "OrderedDict[str, None]" = OrderedDict()


class Receiver(BaseHTTPRequestHandler):
    def answer(self, status: int, text: str) -> None:
        self.send_response(status)
        self.send_header("content-length", str(len(text)))
        self.end_headers()
        self.wfile.write(text.encode())

    def do_POST(self) -> None:
        if self.path != "/webhooks":
            return self.answer(404, "")
        # The raw bytes: parsing and re-serialising the JSON would break the signature.
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            event = verify_webhook(body, self.headers.get(WEBHOOK_SIGNATURE_HEADER), secrets)
        except WebhookSignatureError as e:
            print(f"[receiver] refused a delivery: {e.reason}", flush=True)
            return self.answer(400, e.reason)
        attempt = self.headers.get("Boxline-Attempt")
        if event["id"] in handled:
            print(f"[receiver] duplicate {event['type']} {event['id']} (attempt {attempt}): already handled, ignored", flush=True)
            return self.answer(200, "duplicate")
        handled[event["id"]] = None
        if len(handled) > 10_000:
            handled.popitem(last=False)
        d = event["data"]
        if event["type"] == "session.ended":
            what = f"session {d['id']} {d['status']} ({d['endReason']})"
        elif event["type"] == "webhook.test":
            what = d.get("message", "")
        else:
            what = str(d)[:100]
        print(f"[receiver] verified {event['type']} {event['id']} (attempt {attempt}, delivery {self.headers.get('Boxline-Delivery-Id')}): {what}", flush=True)
        self.answer(200, "ok")  # answer fast; do slow work after answering (or queue it)

    def log_message(self, *args: object) -> None:
        pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Receiver)


def stop(*_: object) -> None:
    if registered:
        client, endpoint_id = registered
        client.webhooks.delete(endpoint_id)
        print(f"[receiver] deleted endpoint {endpoint_id}", flush=True)
    os._exit(0)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
print(f"[receiver] listening on {URL}", flush=True)
server.serve_forever()
