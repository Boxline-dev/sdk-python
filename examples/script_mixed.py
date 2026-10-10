"""A script that mixes Playwright, the shell and files inside the session: step() clicks the download in plain
English, child_process runs Python on the CSV, fs reads the result, and step() types it into the form. useModel()
picks the model for every step() in the script.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/script_mixed.py
Needs shell sessions and a model on the server.
"""

import json
import os

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

provider = next((p for p in bx.agent.models()["providers"] if p["available"]), None)
if provider is None:
    raise SystemExit("no model provider is configured on this server")
fast = "claude-haiku-5-5" if provider["id"] == "anthropic" else "gpt-6-luna"

# The script is JavaScript, run with Node inside the session, next to its browser; page, step() and useModel() are
# in scope, and require() gives Node's own modules.
code = (
    """
const { execSync } = require("child_process");
const fs = require("fs");
useModel(%(model)s);

await page.goto(%(site)s);
await step("click the link that downloads the report");
for (let i = 0; i < 100 && !fs.existsSync("downloads/report.csv"); i++) await new Promise((r) => setTimeout(r, 200));
console.log("downloaded:", fs.statSync("downloads/report.csv").size, "bytes");

fs.mkdirSync("output", { recursive: true });
console.log(execSync(`python3 -c "import csv; t = sum(float(r['revenue']) for r in csv.DictReader(open('downloads/report.csv'))); open('output/total.txt', 'w').write(f'{t:.2f}'); print('python total', f'{t:.2f}')"`).toString().trim());
const total = fs.readFileSync("output/total.txt", "utf8").trim();

await step("type " + total + " into the total revenue field");
await step("press the Send button");
console.log("site:", await page.textContent("#received"));
"""
    % {"model": json.dumps(fast), "site": json.dumps(site)}
)

with bx.sessions.create(shell=True, timeout=300) as session:
    proc = session.run_script(code, timeout_ms=180_000)
    for stream, text in proc:
        print(text, end="")
    print(f"exit {proc.result['exitCode']} in {proc.result['durationMs']} ms")
    print("output/total.txt (files API):", session.files.read_text("output/total.txt").strip())
