"""An agent run with variables: the model sees %email% and %password%, never the values, and the password may only be
typed into fields on the site you name.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/agent_variables.py
Needs a model on the server (ANTHROPIC_API_KEY or OPENAI_API_KEY).
"""

import json
import os
from urllib.parse import urlparse

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
origin = "{0.scheme}://{0.netloc}".format(urlparse(site))
bx = Boxline()

run = bx.agent.run(
    f"Open {site}/login, sign in with %email% and %password%, and tell me the exact text that confirms who is signed in.",
    max_steps=12,
    variables={
        "email": "ada@example.com",
        "password": {"value": "example-password", "origins": [origin]},
    },
)
print(f"run {run['id']} on {run['provider']}/{run['model']}")

for event in bx.agent.stream(run["id"]):
    if event["type"] == "tool":
        print(f"  {event['name']} {json.dumps(event.get('input'))}")  # placeholders, never values
    elif event["type"] == "text" and event.get("text"):
        print(f"  says: {event['text'][:120]}")
    elif event["type"] == "done":
        print(f"done: {event['status']}: {event.get('result') or event.get('error')}")

final = bx.agent.get(run["id"])
print(f"{len(final['steps'])} steps, {final['usage']['inputTokens'] + final['usage']['outputTokens']} tokens, ${final['usage']['costUsd']}")
print("the stored run mentions the password:", "yes (bug!)" if "example-password" in json.dumps(final) else "no")
