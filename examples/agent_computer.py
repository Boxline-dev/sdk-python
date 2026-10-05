"""An agent run in computer mode: the model works on screenshots with its provider's own computer-use tool (Claude's
computer tool, or OpenAI's), and each step shows its thought as it streams.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… BOXLINE_API_URL=http://localhost:8080 python examples/agent_computer.py
Needs a model with computer use on the server (ANTHROPIC_API_KEY or OPENAI_API_KEY).
"""

import json
import os

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

# A configured provider's model with computer use, its default model first.
candidates = [
    (p["id"], m)
    for p in bx.agent.models()["providers"]
    if p["available"]
    for m in sorted(p["models"], key=lambda m: not m["default"])
    if m.get("supportsComputerUse")
]
if not candidates:
    raise SystemExit("no model with computer use is configured on this server")
provider, model = candidates[0]
print(f"computer use with {provider}/{model['id']} (tool {model.get('computerTool')})")

run = bx.agent.run(
    f"Open {site}/drag. Drag the blue box into the dashed zone, then tell me the status text shown below them.",
    mode="computer",
    provider=provider,
    model=model["id"],
    max_steps=15,
)
for e in bx.agent.stream(run["id"]):
    if e["type"] == "thought":
        print(f"  thinks: {e['text']}")
    elif e["type"] == "tool":
        print(f"  {e['name']}: {json.dumps(e.get('input'))[:120]}")
    elif e["type"] == "done":
        print(f"done: {e['status']}: {e.get('result') or e.get('error')}")

done = bx.agent.get(run["id"])
print(f"mode {done.get('mode')}, {len(done['steps'])} steps, ${done['usage']['costUsd']}")
bx.sessions.stop(done["sessionId"])
