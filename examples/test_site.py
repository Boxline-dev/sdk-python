"""A tiny local site for the examples: a CSV report to download, a form to fill, a sign-in page, a stand-in CAPTCHA
widget, a page for the mouse and keyboard (/drag), a page with ads (/ads) and a few pages to crawl. Nothing leaves
your computer, except the requests /ads makes to ad and tracker sites when the session does not block them.

    python examples/test_site.py        # http://127.0.0.1:4800 (PORT=… to change)

The session's browser must be able to reach it: with a local API (RUNTIME=local) that is this computer.
"""

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "4800"))
REPORT = "month,revenue\njan,1200\nfeb,850.50\nmar,1430\n"  # total 3480.50


def page(title: str, body: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body style="font-family:sans-serif;max-width:40rem;margin:2rem auto"><h1>{title}</h1>{body}</body></html>'


INDEX = page(
    "Quarterly report",
    """<p><a id="download" href="/report.csv">Download the report (CSV)</a></p>
<form id="total-form" action="/submit" method="get">
  <label>Total revenue <input id="total" name="total" placeholder="0.00"></label>
  <label>Country <select id="country" name="country"><option>France</option><option>Germany</option><option>Japan</option></select></label>
  <button id="send" type="submit">Send</button>
</form>
<p><a href="/login">Sign in</a> · <a href="/docs">Docs</a> · <a href="/captcha">A page with a CAPTCHA</a> · <a href="/drag">Drag and drop</a> · <a href="/ads">A page with ads</a></p>""",
)
LOGIN = page(
    "Sign in",
    """<form method="post" action="/login"><label>Email <input id="email" name="email" type="email"></label>
<label>Password <input id="password" name="password" type="password"></label><button type="submit">Sign in</button></form>""",
)
# A stand-in for a reCAPTCHA widget (the platform recognises the widget by its frame address and answer field).
CAPTCHA = page(
    "Create an account",
    """<iframe src="/recaptcha/api2/anchor?k=example&size=normal" width="304" height="78" style="border:0"></iframe>
<textarea name="g-recaptcha-response" style="display:none"></textarea>""",
)
# Mouse and keyboard: the box's centre starts at (140, 190); the zone's centre is (460, 210), in CSS pixels.
DRAG = page(
    "Drag and drop",
    r"""<div id="box" style="position:absolute;left:100px;top:150px;width:80px;height:80px;background:#2f7bff;touch-action:none"></div>
<div id="zone" style="position:absolute;left:400px;top:150px;width:120px;height:120px;border:2px dashed #999"></div>
<div style="position:absolute;top:320px">
  <p id="status">not dropped</p>
  <button id="menu" onmouseover="document.getElementById('tip').hidden = false">Hover me</button> <span id="tip" hidden>Tooltip shown</span>
  <button id="dbl" ondblclick="this.textContent = 'Double-clicked'">Double-click me</button>
  <input id="note" value="select me and replace me">
</div>
<script>
  const box = document.getElementById("box"), zone = document.getElementById("zone"), status = document.getElementById("status");
  let grab = null;
  box.addEventListener("pointerdown", (e) => { grab = { dx: e.clientX - box.offsetLeft, dy: e.clientY - box.offsetTop }; });
  addEventListener("pointermove", (e) => { if (grab) { box.style.left = e.clientX - grab.dx + "px"; box.style.top = e.clientY - grab.dy + "px"; } });
  addEventListener("pointerup", () => {
    if (!grab) return;
    grab = null;
    const b = box.getBoundingClientRect(), z = zone.getBoundingClientRect();
    const inside = b.left >= z.left && b.right <= z.right && b.top >= z.top && b.bottom <= z.bottom;
    status.textContent = inside ? "dropped in the zone" : "dropped outside the zone";
  });
</script>""",
)
# Five requests to sites on the ad and tracker list; blockAds refuses them inside the session's machine.
ADS = page(
    "An article",
    """<p>An article with ads: it asks five ad and tracker sites for files (made-up file names, so none of them would serve an ad).</p>
<p id="text">Revenue grew in every month of the quarter.</p>
<img src="https://securepubads.g.doubleclick.net/boxline-example/banner.gif" width="300" height="50" alt="">
<img src="https://www.google-analytics.com/boxline-example/pixel.gif" width="1" height="1" alt="">
<img src="https://sb.scorecardresearch.com/boxline-example/beacon.gif" width="1" height="1" alt="">
<script async src="https://static.criteo.net/boxline-example/tag.js"></script>
<script async src="https://static.hotjar.com/boxline-example/tracker.js"></script>""",
)
PAGES = {
    "/": INDEX,
    "/drag": DRAG,
    "/ads": ADS,
    "/login": LOGIN,
    "/captcha": CAPTCHA,
    "/recaptcha/api2/anchor": '<!doctype html><title>widget</title><div style="width:300px;height:74px;background:#eee">I\'m not a robot</div>',
    "/docs": page("Docs", '<a href="/docs/start">Start</a> <a href="/docs/api">API</a>'),
    "/docs/start": page("Getting started", '<p>Install the SDK.</p><a href="/docs/api">API</a>'),
    "/docs/api": page("API", '<p>Every route.</p><a href="/docs">Docs</a>'),
}


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8", extra: dict = {}) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/report.csv":
            return self.send(200, REPORT, "text/csv", {"content-disposition": 'attachment; filename="report.csv"'})
        if url.path == "/submit":
            q = parse_qs(url.query)
            total = html.escape(q.get("total", [""])[0])
            country = html.escape(q.get("country", [""])[0])
            return self.send(200, page("Thank you", f'<p id="received">Received total: {total}</p><p>Country: {country}</p>'))
        if url.path in PAGES:
            return self.send(200, PAGES[url.path])
        self.send(404, page("Not found", ""))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/login":
            return self.send(404, page("Not found", ""))
        form = parse_qs(self.rfile.read(int(self.headers.get("content-length") or 0)).decode())
        email = form.get("email", [""])[0]
        ok = bool(email) and form.get("password", [""])[0] == "example-password"
        if ok:
            self.send(200, page("Signed in", f'<p id="who">Signed in as {html.escape(email)}</p>'))
        else:
            self.send(401, page("Wrong password", '<p><a href="/login">Try again</a></p>'))

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    print(f"example site on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
