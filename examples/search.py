"""Web search: results with titles, URLs, snippets and dates; with ``fetch``, the top pages as Markdown too (opened in
a sandboxed browser, like bx.fetch). The same search within an hour comes from the cache and is not counted. The
query text goes to the search provider: keep secrets and personal data out of it.

    BOXLINE_API_KEY=bxl_… python examples/search.py ["your query"]
"""

import sys

from boxline import Boxline, SearchUnavailableError

query = sys.argv[1] if len(sys.argv) > 1 else "playwright connectOverCDP"
bx = Boxline()

try:
    r = bx.search(query, limit=5, safe_search="strict")
    print(f"\"{r['query']}\": {len(r['results'])} results in {r['ms']} ms (cached: {r['cached']})")
    for x in r["results"]:
        extra = "".join(f" · {v}" for v in (x.get("siteName"), (x.get("publishedAt") or "")[:10]) if v)
        print(f"- {x['title']}{extra}\n  {x['url']}\n  {x['snippet'][:110]}")

    with_pages = bx.search(query, limit=3, fetch=2)
    for x in with_pages["results"]:
        if "content" not in x:
            continue
        if x["content"]:
            print(f"fetched {x['page']['finalUrl']}: HTTP {x['page']['status']}, {len(x['content'])} characters of Markdown")
        else:
            print(f"could not open {x['url']}: {(x.get('error') or {}).get('code')}")

    again = bx.search(query, limit=5, safe_search="strict")
    print("the same search again: cached =", again["cached"])
    searches = bx.usage()["searches"]
    print(f"searches this month: {searches['count']} (${searches['costUsd']} beyond the plan's allowance)")
except SearchUnavailableError as e:
    print("search is not set up on this server:", e.message)
