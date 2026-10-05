"""The async client: the same methods as Boxline, awaited. Several calls run at once with asyncio.gather.

    BOXLINE_API_KEY=bxl_… python examples/async_quickstart.py
"""

import asyncio
import os

from boxline import AsyncBoxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")


async def main() -> None:
    async with AsyncBoxline() as bx:
        # Three pages fetched at the same time, each in its own sandboxed browser.
        pages = await asyncio.gather(*(bx.fetch(f"{site}{path}") for path in ("/", "/docs", "/docs/api")))
        for p in pages:
            print(f"fetch {p['finalUrl']}: {p['title']} ({p['ms']} ms)")

        async with await bx.sessions.create(timeout=120, user_metadata={"from": "async"}) as s:
            print("goto:", (await s.goto(site))["title"])
            print("elements:", (await s.elements())["count"], "interactive elements")
            async for listed in bx.sessions.list(status="RUNNING"):
                print("running:", listed.id)
        print("stopped:", s.status)


asyncio.run(main())
