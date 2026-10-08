"""An agent that uses the browser AND the shell in one run: it downloads a report in the browser, adds it up with
Python in the shell, writes output/total.txt, and types the total into the site's form. Each step is printed as it
streams (browser_* tools, then bash, then browser_* again); afterwards the file is read with the files API.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/agent_mixed.py
Needs a model on the server (ANTHROPIC_API_KEY or OPENAI_API_KEY) and shell sessions.
"""

import json
import os

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

run = bx.agent.run(
    f"Go to {site} and download the report (the CSV link). With Python in the shell, add up the revenue column of "
    f"downloads/report.csv and write the total with two decimals to output/total.txt. Then enter that total in the "
    f'"Total revenue" field of the form on {site}, press Send, and reply with the total and what the site answered.',
    session={"shell": True},
    keep_session=True,  # keep the session afterwards, to read output/total.txt
    max_steps=30,
)
print(f"run {run['id']} ({run['provider']}/{run['model']}) in session {run['sessionId']}")

# Browser tools arrive as steps once they finish; a shell command arrives as "exec" when it starts, then its output.
n = 0
for e in bx.agent.stream(run["id"]):
    if e["type"] == "tool" and e.get("name") != "bash":
        n += 1
        print(f"{n:2}. {e['name']}{' (error)' if e.get('isError') else ''}: {json.dumps(e.get('input') or {})[:110]}")
    elif e["type"] == "exec":
        n += 1
        print(f"{n:2}. bash: {e['command'].splitlines()[0][:110]}")
    elif e["type"] == "output":
        print("      | " + e["data"].strip().replace("\n", "\n      | "))
    elif e["type"] == "done":
        print(f"done: {e['status']}\n{e.get('result') or e.get('error')}")

with bx.sessions.get(run["sessionId"]) as session:  # released when the block ends
    print("output/total.txt:", session.files.read_text("output/total.txt").strip())
