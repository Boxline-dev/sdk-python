"""A Playwright script run inside the session, with plain-English steps: useModel() picks the model for every later
step() and extract(); a call can still name its own.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/script_use_model.py
Needs a session with a shell and a model on the server.
"""

import json
import os

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

# The fast step model of whichever provider this server has.
provider = next((p for p in bx.agent.models()["providers"] if p["available"]), None)
if provider is None:
    raise SystemExit("no model provider is configured on this server")
fast = "claude-haiku-4-5" if provider["id"] == "anthropic" else "gpt-6-luna"

# The script is JavaScript (Playwright), run with Node inside the session, next to its browser.
code = f"""
useModel({json.dumps(fast)});
await page.goto({json.dumps(site)});
const typed = await step("type 3480.50 into the total revenue field");
console.log("step:", typed.description, "->", typed.code);
await step("choose Germany as the country");
// Inside a script, extract() returns the data itself. It reads the page's text (typed values are not text).
const links = await extract("the page heading and the text of each link", {{ type: "object", properties: {{ heading: {{ type: "string" }}, links: {{ type: "array", items: {{ type: "string" }} }} }} }});
console.log("extract:", JSON.stringify(links));
await step("press the Send button");
console.log("page:", await page.title(), "-", await page.textContent("#received"));
"""

with bx.sessions.create(shell=True, timeout=300) as session:
    with session.run_script(code, timeout_ms=180_000) as proc:
        for stream, text in proc:
            print(text, end="")
    print(f"exit {proc.result['exitCode']} in {proc.result['durationMs']} ms")
