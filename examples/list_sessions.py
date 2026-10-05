"""Cursor pages: a list call returns its first page; iterating it yields every item (the SDK follows ``next``).

    BOXLINE_API_KEY=bxl_… python examples/list_sessions.py
"""

import time

from boxline import Boxline

bx = Boxline()
tag = f"example-{time.time_ns()}"

made = [bx.sessions.create(timeout=120, user_metadata={"tag": tag}) for _ in range(3)]
print(f"started {len(made)} sessions tagged {tag}")

first = bx.sessions.list(q=tag, limit=2)
print(f"first page: {len(first.data)} of {first.total}, next page: {'yes' if first.has_next_page() else 'no'}")

for n, s in enumerate(bx.sessions.list(q=tag, limit=2), 1):
    print(f"  {n}. {s.id} {s.status} created {s.created_at}")

for page in first.iter_pages():
    print(f"page with {len(page.data)} sessions, next = {page.next}")

results = bx.sessions.bulk("delete", [s.id for s in made])["results"]
print(f"deleted: {sum(r['ok'] for r in results)}/{len(results)}")
