"""Browser, shell and files in ONE session: Playwright downloads a CSV, Python in the session's shell adds up a
column, the files API reads the result, and the actions API types it into a form. The browser's downloads and the
shell share the same /workspace disk.

    pip install "boxline-sdk[playwright]"
    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/mixed_download_process_fill.py
"""

import os

from playwright.sync_api import sync_playwright

from boxline import Boxline

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

with bx.sessions.create(shell=True, timeout=300) as session:
    print(f"session {session.id}: browser + shell, workspace {session.workspace_path}")

    with sync_playwright() as p:
        # 1. The browser (Playwright over CDP): open the page and click the download link.
        browser = p.chromium.connect_over_cdp(session.connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(site)
        page.click("#download")

        # 2. Files: wait until the download has finished writing (downloads always land in <workspace>/downloads).
        file = session.files.wait_for("downloads/*.csv", timeout_ms=30_000)
        print(f"downloaded {file['path']} ({file['size']} bytes)")

        # 3. The shell: Python adds up the revenue column and writes output/total.txt.
        r = session.exec(
            """mkdir -p output && python3 - <<'EOF'
import csv
with open("downloads/report.csv", newline="") as f:
    total = sum(float(row["revenue"]) for row in csv.DictReader(f))
with open("output/total.txt", "w") as out:
    out.write(f"{total:.2f}\\n")
print(f"total revenue: {total:.2f}")
EOF"""
        )
        print(f"python (exit {r['exitCode']}): {r['stdout'].strip()}")

        # 4. Files again: read the result the shell wrote.
        total = session.files.read_text("output/total.txt").strip()
        print(f"output/total.txt: {total}")

        # 5. The actions API (same browser): fill the form and send it.
        results = session.actions(
            [
                {"action": "fill", "selector": "#total", "value": total},
                {"action": "click", "selector": "#send"},
                {"action": "wait", "selector": "#received"},
                {"action": "evaluate", "expression": "document.querySelector('#received').textContent"},
            ]
        )
        print(f"the site says: {results[-1]['value']}")
        browser.close()
