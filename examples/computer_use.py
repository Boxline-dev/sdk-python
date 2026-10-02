"""Mouse, keyboard and computer use on a session's page: drag with selectors, hover, double-click, select-all and
type, the one-line result of every action, and computer-use actions in Anthropic's and OpenAI's shapes on a
scaled-down screenshot. Everything acts on the page through the browser, never on the machine's desktop.

    python examples/test_site.py &      # its /drag page
    BOXLINE_API_KEY=bxl_… BOXLINE_API_URL=http://localhost:8080 python examples/computer_use.py
"""

import base64
import json
import os

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

with bx.sessions.create(timeout=300) as s:  # viewport 1280×720: coordinates are CSS pixels of it

    def text(element_id: str, what: str = "textContent") -> str:
        return s.evaluate(f"String(document.getElementById({json.dumps(element_id)}).{what})")

    s.goto(f"{site}/drag")

    # The mouse: drag the box into the zone (a selector means the element's centre), in 20 steps.
    d = s.mouse.drag("#box", "#zone", steps=20)
    print(f"drag ({d['from']['x']}, {d['from']['y']}) → ({d['to']['x']}, {d['to']['y']}): {text('status')}")
    s.hover("#menu")
    print("hover:", "tooltip shown" if text("tip", "hidden") == "false" else "tooltip hidden")
    s.click("#dbl", count=2)
    print(f"double click: {text('dbl')}; the pointer is at {s.cursor()}")

    # The keyboard: select everything in the field and type over it.
    s.click("#note")
    s.keyboard.key("ControlOrMeta+A")
    s.keyboard.type("replaced")
    print("field:", text("note", "value"))

    # Every action says what it did; a bare string is a plain-English step (it needs a model on the server).
    results = s.actions([{"action": "move", "x": 640, "y": 400, "steps": 5}, {"action": "scroll", "deltaY": 200}, {"action": "key", "keys": "Escape"}, "move the mouse over the Hover me button"])
    for r in results:
        print(f"  {r['action']}: {r.get('text') or r.get('error')}")

    # Computer use: actions exactly as a model's computer tool gives them, on a 640-pixel-wide screenshot (scale 0.5,
    # so the box's centre (140, 190) is (70, 95) and the zone's (460, 210) is (230, 105)).
    s.goto(f"{site}/drag")
    a = s.computer({"action": "left_click_drag", "start_coordinate": [70, 95], "coordinate": [230, 105]}, max_width=640)
    print(f"anthropic {a['action']}: {a['text']} ({a['width']}×{a['height']}, scale {a['scale']}) → {text('status')}")
    o = s.computer({"type": "move", "x": 320, "y": 180}, max_width=640, cursor=True)
    print(f"openai {o['action']}: {o['text']}; cursor ({o['cursor']['x']}, {o['cursor']['y']}); \"{o['title']}\"")
    with open("computer.png", "wb") as f:
        f.write(base64.b64decode(o["screenshot"]))
    print(f"computer.png: the screen with the pointer drawn on it ({o['mimeType']})")
