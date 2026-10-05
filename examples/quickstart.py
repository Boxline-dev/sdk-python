"""Quickstart: a session, Playwright over CDP, and the actions API on the same browser.

    pip install "boxline-sdk[playwright]"
    BOXLINE_API_KEY=bxl_… python examples/quickstart.py [url]
"""

import os
import sys

from playwright.sync_api import sync_playwright

from boxline import Boxline

url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SITE_URL", "https://example.com")
bx = Boxline()  # BOXLINE_API_KEY; BOXLINE_API_URL (default https://api.boxline.dev)

with bx.sessions.create(timeout=300) as session:  # leaving the block stops it
    print(f"session {session.id} ({session.status})")

    # Playwright drives the session's browser directly. connect_url is a signed URL: treat it like a password.
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(session.connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url)
        print("Playwright sees:", page.title())
        browser.close()  # disconnects this client; the session keeps running

    # The actions API runs next to the same browser, one HTTP call per list of actions.
    content = session.content("markdown")
    print(f"actions API sees: {content['title']} ({len(content['content'])} characters of markdown)")
    png = session.screenshot()
    print(f"screenshot: {len(png)} bytes")

print("stopped:", session.status, session.stop_reason)
