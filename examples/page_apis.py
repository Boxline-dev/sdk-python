"""The page APIs: one call each, rendered in a real browser inside a sandbox (never on your computer or ours).

    python examples/test_site.py &      # or pass any public URL
    BOXLINE_API_KEY=bxl_… python examples/page_apis.py [url]
"""

import os
import sys
from urllib.parse import urljoin

from boxline import Boxline, PageUnreachableError

url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

page = bx.fetch(url, format="markdown", links=True, delay_ms=200, viewport={"width": 1280, "height": 800})
print(f"fetch: {page['status']} \"{page['title']}\", {len(page['content'])} characters, {len(page.get('links', []))} links, CAPTCHA: {page['captcha'] or 'none'}")

png = bx.screenshot(url, full_page=True)
open("page.png", "wb").write(png)
print(f"screenshot: page.png ({len(png)} bytes)")

pdf = bx.pdf(url, paper="A4")
open("page.pdf", "wb").write(pdf)
print(f"pdf: page.pdf ({len(pdf)} bytes)")

job = bx.crawl.wait(bx.crawl.start(urljoin(url, "/docs"), max_pages=5, max_depth=1)["id"], poll=0.5)
print(f"crawl: {job['status']}, {len(job['data'])} pages: {', '.join(p['title'] or '?' for p in job['data'])}")

# extract needs a model on the server (ANTHROPIC_API_KEY or OPENAI_API_KEY).
if any(p["available"] for p in bx.agent.models()["providers"]):
    r = bx.extract(url=url, schema={"type": "object", "properties": {"title": {"type": "string"}, "links": {"type": "integer"}}, "required": ["title", "links"]})
    print(f"extract ({r['model']}): {r['data']} ${r['usage']['costUsd']}")
else:
    print("extract: skipped (no model provider is configured on this server)")

try:
    bx.fetch("http://127.0.0.1:1/")
except PageUnreachableError as e:
    print(f"a page that cannot load: {e.status} {e.code}: {e.message} (request {e.request_id})")
