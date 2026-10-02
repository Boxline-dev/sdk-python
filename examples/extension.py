"""A Chrome extension in a session: build a tiny Manifest V3 extension (a content script that changes the page's
title) as a zip, upload it once, start a session with it, and read the title the page ends up with.

    python examples/test_site.py &
    BOXLINE_API_KEY=bxl_… python examples/extension.py

Needs the plan feature ``extensions``. An extension sees every page and every value typed into the sessions that use
it, and can send them anywhere: upload only extensions you trust, and keep secrets in sessions without extensions.
A zip file on disk works too: ``bx.extensions.upload("my-extension.zip")``.
"""

import io
import json
import os
import sys
import time
import zipfile
from urllib.parse import urlparse

from boxline import Boxline, FeatureNotInPlanError

site = os.environ.get("SITE_URL", "http://127.0.0.1:4800")
bx = Boxline()

# Least privilege: no permissions, and the content script runs on the example site's host only.
u = urlparse(site)
manifest = {
    "manifest_version": 3,
    "name": "Boxline title example",
    "version": "1.0",
    "description": "Prefixes the page title.",
    "content_scripts": [{"matches": [f"{u.scheme}://{u.hostname}/*"], "js": ["title.js"], "run_at": "document_end"}],
}
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("manifest.json", json.dumps(manifest, indent=2))
    z.writestr("title.js", 'document.title = "Changed by an extension: " + document.title;\n')

try:
    ext = bx.extensions.upload(buf.getvalue())
except FeatureNotInPlanError:
    sys.exit("this project's plan has no extensions (plan feature `extensions`)")
# (InvalidExtensionError says what is wrong with a zip.)
print(f'uploaded {ext["id"]}: "{ext["name"]}" {ext["version"]}, {ext["files"]} files, {ext["sizeBytes"]} bytes, permissions {ext["permissions"]}')

try:
    with bx.sessions.create(extensions=[ext["id"]], timeout=300) as s:
        print(f"session {s.id} with extensions {s.extensions}")
        loaded = [e for e in s.events(types=["lifecycle"]) if e.get("text") == "extensions loaded"]
        if loaded:
            print(f"extensions loaded in {(loaded[0].get('data') or {}).get('ms')} ms")

        s.goto(site)
        title = s.evaluate("document.title")
        for _ in range(10):
            if title.startswith("Changed"):
                break
            time.sleep(0.3)
            title = s.evaluate("document.title")
        print("page title:", title)
finally:
    # Sessions already running with it keep it; new ones can't use it any more.
    bx.extensions.delete(ext["id"])
    print("deleted", ext["id"])
