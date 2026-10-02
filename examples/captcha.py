"""CAPTCHAs: by default ("ask") the platform notices a CAPTCHA waiting for a person and hands over; you get told, a
person solves it in the live view, and your code (or the agent) carries on. Only automate sites you are allowed to.

    python examples/test_site.py &      # its /captcha page has a stand-in widget
    BOXLINE_API_KEY=bxl_… python examples/captcha.py
"""

import os
import time

from boxline import Boxline, CaptchaTimeoutError

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

with bx.sessions.create(captcha="ask", timeout=300) as session:
    session.goto(f"{site}/captcha")
    time.sleep(5)  # detection looks twice, about 2 s apart
    for event in session.events(types=["captcha"]):
        print(f"captcha event: {event['data']['state']} ({event['data']['kind']}) on {event.get('url')}")
    waiting = session.refresh().data["attention"]
    if waiting:
        print(f"waiting for a person: {waiting['kind']} ({waiting.get('state', 'waiting')}); send them the live view (session.live_url)")

    # Here the example site's stand-in widget is answered in its place (what a person's click does on a real one).
    session.evaluate('document.querySelector("[name=g-recaptcha-response]").value = "answered-by-a-person"')
    try:
        session.wait_for_human(timeout=30)
        print("solved; carrying on. attention =", session.data["attention"])
    except CaptchaTimeoutError as e:
        print("nobody solved it in time:", e.message)
