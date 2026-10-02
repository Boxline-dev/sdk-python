"""Ad and tracker blocking: a session with ``block_ads=True`` opens a page that asks five ad and tracker sites for
files. The machine's local proxy refuses those requests before they leave it (they cost no proxy data), and the
session's ``blocked_requests`` counts them. The count is collected from the machine about every 30 seconds, so this
waits for it.

    python examples/test_site.py &      # its /ads page
    BOXLINE_API_KEY=bxl_… python examples/block_ads.py

``block_ads`` works on every plan, and on the quick APIs too: ``bx.fetch(url, block_ads=True)``, screenshot, pdf,
extract and crawl.start.
"""

import os
import time

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

# cookie_banners="reject" is the default: consent banners are answered "Reject all" / "Necessary only", never accepted.
with bx.sessions.create(block_ads=True, cookie_banners="reject", timeout=300) as s:
    print(f'session {s.id}: blockAds {s.block_ads}, cookie banners "{s.cookie_banners}"')
    print("opened", s.goto(f"{site}/ads")["url"])

    until = time.monotonic() + 90
    while s.blocked_requests == 0 and time.monotonic() < until:
        time.sleep(3)
        s.refresh()
    print("blocked requests:", s.blocked_requests)

    # The session's log says which list entries were hit (the machine's most blocked, at most 10).
    reports = [e for e in s.events(types=["lifecycle"]) if e.get("text") == "ads and trackers blocked"]
    top = (reports[-1].get("data") or {}).get("top", []) if reports else []
    if top:
        print("most blocked:", ", ".join(f"{t['domain']} ({t['count']})" for t in top))
