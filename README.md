# Boxline Python SDK

**Give your AI agents the infrastructure they need: browsers, shells, storage and isolated machines.**

Each session is an isolated machine with a real Chrome and, if you
ask for it, a bash shell with Python and Node, sharing one `/workspace` disk. Sync (`Boxline`) and async
(`AsyncBoxline`) clients with the same methods.

```bash
pip install boxline-sdk             # add [playwright] to drive the browser with Playwright: "boxline-sdk[playwright]"
export BOXLINE_API_KEY=bxl_...
```

The package is `boxline-sdk` on PyPI (the name `boxline` there is another project); you import it as `boxline`.
The client talks to `https://api.boxline.dev`; set `BOXLINE_API_URL` (or `base_url=`) for another API, e.g.
`http://localhost:8080` for a local one.

Python 3.9 or newer. Responses are plain dicts with the API's camelCase keys, typed with TypedDicts
(`boxline.types`); the package ships `py.typed`.

- [Sessions and Playwright](#sessions-and-playwright)
- [Browser, shell and files together](#browser-shell-and-files-together)
- [The page APIs](#the-page-apis)
- [Web search](#web-search)
- [Mouse, keyboard and computer use](#mouse-keyboard-and-computer-use)
- [An agent run with variables](#an-agent-run-with-variables)
- [Limits, Continue and messages](#limits-continue-and-messages)
- [Tasks and structured output](#tasks-and-structured-output)
- [Credentials and browser profiles](#credentials-and-browser-profiles)
- [A script with useModel](#a-script-with-usemodel)
- [CAPTCHAs](#captchas)
- [Browser settings and extensions](#browser-settings-and-extensions)
- [Webhooks](#webhooks)
- [Async](#async)
- [Lists and pages](#lists-and-pages)
- [Errors, retries and time limits](#errors-retries-and-time-limits)
- [Every method](#every-method)
- [Examples](#examples)

## Sessions and Playwright

```python
from playwright.sync_api import sync_playwright
from boxline import Boxline

bx = Boxline()  # BOXLINE_API_KEY; BOXLINE_API_URL (default https://api.boxline.dev)

with bx.sessions.create(timeout=300) as session:      # leaving the block releases it
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(session.connect_url)  # a signed URL: treat it like a password
        page = browser.contexts[0].pages[0]
        page.goto("https://example.com")
        print(page.title())

    # The actions API drives the same browser without Playwright, one HTTP call per list of actions.
    print(session.content("markdown")["title"])
```

`session.live_url` is a page where a person can watch and take over. `session.pause()` saves the browser and files
and stops billing; touching the session resumes it.

## Browser, shell and files together

One session has a browser, a shell and a disk they share: browser downloads land in `<workspace>/downloads`, the
shell starts in the workspace, and the files API reads and writes it.

```python
with bx.sessions.create(shell=True) as s, sync_playwright() as p:
    page = p.chromium.connect_over_cdp(s.connect_url).contexts[0].pages[0]
    page.goto("http://127.0.0.1:4800")
    page.click("#download")                                   # the browser downloads a CSV

    s.files.wait_for("downloads/*.csv")                       # files: wait until it has finished writing
    s.exec("""mkdir -p output && python3 - <<'EOF'
import csv
total = sum(float(row["revenue"]) for row in csv.DictReader(open("downloads/report.csv")))
open("output/total.txt", "w").write(f"{total:.2f}")
EOF""")                                                       # the shell: Python adds up a column
    total = s.files.read_text("output/total.txt").strip()      # files: read what the shell wrote

    s.actions([{"action": "fill", "selector": "#total", "value": total}, {"action": "click", "selector": "#send"}])
```

The same mix works inside an agent run (`shell=True`: the agent uses `browser_*` tools and `bash`) and inside a script
(`require("child_process")` and `require("fs")` next to `page` and `step()`): see
[agent_mixed.py](examples/agent_mixed.py) and [script_mixed.py](examples/script_mixed.py).

## The page APIs

One call each; the page renders in a real browser inside a sandbox.

```python
page = bx.fetch("https://example.com", format="markdown", links=True)
png = bx.screenshot("https://example.com", full_page=True)   # bytes
pdf = bx.pdf("https://example.com", paper="A4")
data = bx.extract(url="https://example.com/pricing", schema={"type": "object", "properties": {"plans": {"type": "array"}}})["data"]
job = bx.crawl.wait(bx.crawl.start("https://example.com/docs", max_pages=20)["id"])
```

A page that cannot load raises `PageUnreachableError` (502 `page_unreachable`) or `PageTimeoutError` (504
`page_timeout`).

## Web search

```python
found = bx.search("playwright connectOverCDP", limit=5, country="US", recency="year")
for r in found["results"]:
    print(r["title"], r["url"], r.get("publishedAt", ""))

# fetch: also open the top pages (True = 3, or 0–5) in a sandboxed browser and get them as Markdown.
with_pages = bx.search("boxline docs", limit=3, fetch=2)
print((with_pages["results"][0].get("content") or "")[:200], with_pages["results"][0].get("error"))
```

The same search by the same project within an hour comes from the cache (`cached: True`) and is not counted; the plan
includes `searchesPerMonth` (`bx.usage()["searches"]`). The query text goes to the search provider (Brave) with the
platform's key, nothing about you: keep passwords and personal data out of it. `SearchUnavailableError` (503
`search_unavailable`) when search is not set up on the server.

## Mouse, keyboard and computer use

Coordinates are CSS pixels of the session's viewport (1280×720 by default). Everything acts on the page through the
browser, never on the machine's desktop, and moves in straight lines (`steps` spreads a move over evenly spaced points).

```python
s.mouse.move(400, 300, steps=10)
s.mouse.click(400, 300, button="right", count=2, modifiers=["Shift"])
s.mouse.drag("#card", "#done-column", steps=20)         # selectors mean the element's centre
s.mouse.drag(path=[(100, 100), (200, 150), (300, 100)])  # or a path
s.mouse.down(); s.mouse.up()
s.hover("#menu")
s.keyboard.key("ControlOrMeta+A")                        # Playwright's key names; ctrl, cmd, Return work too
print(s.cursor())                                        # {"x", "y"}
png = s.screenshot(max_width=640, cursor=True)

# Every result says what happened; a bare string is a plain-English step.
results = s.actions([{"action": "goto", "url": "https://example.com"}, "click More information"])
print([r.get("text") for r in results])
```

**Computer use.** `session.computer()` runs ONE action exactly as a computer-use model's tool gave it (Claude's
`computer` tool input, or one OpenAI `computer_call` action) and returns the screen after it, so you can drive a
session from your own computer-use loop:

```python
screen = s.computer({"action": "left_click", "coordinate": [512, 300]}, max_width=1024)  # Anthropic shape
s.computer({"type": "keypress", "keys": ["CTRL", "A"]}, screenshot=False)                 # OpenAI shape
# screen: {"ok", "text", "screenshot" (base64), "width", "height", "scale", "cursor", "url", "title"}
```

With `max_width` the screenshot is scaled down and the action's coordinates are read in its pixels: use the same
`max_width` on every call. A point outside the screen raises `OutOfViewportError`. Or let the agent do it:
`bx.agent.run(task, mode="computer")` (models with `supportsComputerUse` in `bx.agent.models()`); its steps carry the
model's `thought`, also streamed as `thought` events.

## An agent run with variables

The model sees `%email%` and `%password%`, never the values. A value is typed only where the model types text or
picks an option, and `origins` limits it to fields on those sites (recommended for passwords).

```python
run = bx.agent.run(
    "Sign in to https://example.com with %email% and %password%, then open the billing page.",
    variables={
        "email": "ada@example.com",
        "password": {"value": os.environ["SITE_PASSWORD"], "origins": ["https://example.com"]},
    },
)
for event in bx.agent.stream(run["id"]):
    if event["type"] == "tool":
        print(event["name"], event.get("input"))  # placeholders, never values
    elif event["type"] == "done":
        print(event["status"], event.get("result"))
```

`agent.takeover(run_id)` and `agent.hand_back(run_id, note)` hand the browser to a person and back; `agent.wait(run_id)`
polls until the run ends.

## Limits, Continue and messages

A run stops at the first of its limits: `max_steps` (default 30; `max_steps=None` for none), `max_cost_usd` (optional,
model cost in USD), `max_consecutive_errors` tool errors in a row (default 5), the same call with the same result 5
times, or its session's time (`timeout`, for the run's own session). At a step, cost, error or no-progress limit it ends
with an `errorCode` (`max_steps`, `max_cost`, `too_many_errors`, `no_progress`), `resultText` says what is done and
what is left, and `continuable["until"]` says how long it can be continued: its session is kept for 10 minutes.

```python
run = bx.agent.run("Turn every video in the workspace into 30-second clips", shell=True, max_steps=20, max_cost_usd=2)
done = bx.agent.wait(run["id"])
if done.get("continuable"):
    print(done["resultText"])  # what is done, what is left
    nxt = bx.agent.continue_run(done["id"], max_steps=30, instruction="The downloads are done; do the clips.")
    done = bx.agent.wait(nxt["id"])  # nxt["continuedFrom"] == done["id"], same session
```

A run that had `variables` needs them again on `continue_run` (their values are never stored). `NotContinuableError`:
the run did not stop at a limit, was continued already, or its window passed.

Tell a working run something without taking the browser: it reads the message at its next step, and a run waiting for
your help (`ask_user_for_help`) takes it as the answer.

```python
bx.agent.send_message(run["id"], "Also open page C and include its heading in the answer.")
```

## Tasks and structured output

A task is a saved agent run: an instruction with `%name%` variables, an output schema, browser settings, a profile,
a model and, if you like, a schedule. Run it by hand or on its schedule; every run is an agent run.

```python
task = bx.tasks.create(
    "Books by category",
    "Open https://books.toscrape.com, open the category %category% and return the first 3 books with their prices",
    variables=[{"name": "category", "default": "Travel"}],
    output={
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "books": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}, "required": ["title", "price"]}},
        },
        "required": ["category", "books"],
    },
    schedule={"cron": "0 9 * * MON-FRI", "timezone": "Europe/London", "enabled": False},
)

run = bx.tasks.run(task["id"], variables={"category": "Poetry"})
done = bx.tasks.wait_for_run(run)                  # done["result"]: the JSON answer (a dict)
print(done["status"], done["result"], done["resultText"])

for r in bx.tasks.runs(task["id"], status=["failed", "missed"]):
    print(r["id"], r["errorCode"] or r["reason"])
bx.tasks.update(task["id"], schedule={"enabled": True})   # None removes a field, e.g. schedule=None
```

- **Structured output** works on one agent run too: `bx.agent.run(task, output=schema)`, then `bx.agent.wait(run_id)`.
  `result` is the JSON answer and `resultText` a one-sentence summary. An answer that still does not match after one
  repair try fails the run with `errorCode` "output_invalid" (`ErrorCode.OUTPUT_INVALID`).
- **Variables**: plain ones are written into the instruction (and kept with the run); `{"name", "secret": True,
  "origins"}` is never stored, must come with every run, and is typed without the model seeing it. A task with a
  secret variable cannot have a schedule. A run missing a value raises `MissingVariablesError`.
- **Credentials**: `credentials=["SHOP"]` on `tasks.create` gives every run the saved credentials' placeholders (see
  [Credentials and browser profiles](#credentials-and-browser-profiles)); a scheduled task may use them, and the task
  keeps the names only.
- **Schedules**: five-field cron (at most every 5 minutes) read in `timezone`. A scheduled run is `queued` until it
  starts; a time that comes while a run is still going is `skipped`, and times the platform was down for are `missed`
  (`reason`, `missedCount`). The plan limits tasks and schedules switched on (`PlanLimitError`).
- `wait_for_run` takes the run from `run()` or `(task_id, task_run_id)`, with `poll=` and `timeout=` in seconds; there
  is no GET for one task run, so it watches the task's unfinished runs. `AsyncBoxline` has the same methods.

## Credentials and browser profiles

Credentials are write-only: the value is sealed when stored and never returned or shown. There are two types, a website
**password** (sites, user name, password and an optional 2FA key) and a **secret** (one value, such as an API token).
The name is the handle: the AI's placeholder (`%GITHUB_TOKEN%`, `%SHOP.password%`) and the shell variable
(`$GITHUB_TOKEN`, `$SHOP_PASSWORD`). Each credential's `scope` says where it may be used: `"agent"` (the default: only
the AI), `"shell"` (only as variables in shells) or `"all"`.

```python
bx.credentials.create("GITHUB_TOKEN", "secret", value=os.environ["GITHUB_TOKEN"], scope="shell")
shop = bx.credentials.create(
    "SHOP",
    "password",
    origins=["https://shop.example.com"],          # the only site the AI may type it on
    username="ops@example.com",
    password=os.environ["SHOP_PASSWORD"],
    totp_secret=os.environ.get("SHOP_2FA_KEY"),    # optional: %SHOP.otp% is the current code
)
print(shop["type"], shop["username"], shop["hasTotp"])  # no value anywhere

# In a shell: $GITHUB_TOKEN, shown as %GITHUB_TOKEN% wherever it appears in the output.
s = bx.sessions.create(shell=True, env={"REGION": "eu"}, credentials=["GITHUB_TOKEN"])
s.exec("gh repo list --limit 3")
s.exec("./deploy.sh", credentials=["DEPLOY_KEY"])             # this one command only

# For the AI: typed only on the credential's sites, never shown to the model.
s.step("sign in with %SHOP.username% and %SHOP.password%", credentials=["SHOP"])
bx.agent.run("Sign in to https://shop.example.com with %SHOP.username% and %SHOP.password%", credentials=["SHOP"])
# Or type one field yourself without seeing it (the field's own frame must be on one of the credential's sites):
s.type_credential("SHOP", "password", "#password")

# A profile keeps cookies; link a password and the AI can sign in again when they expire.
bx.profiles.update(profile["id"], credential="SHOP")

for e in bx.credentials.audit(name="SHOP"):
    print(e["at"], e["action"], e.get("actor"), (e.get("usedBy") or {}).get("type"))
```

- **Exported credentials can be read by anything that runs in the shell**, including an agent's commands that a web
  page tries to steer. Export only what you accept that for; keep website passwords at scope `"agent"` with `origins`.
  Hiding values in output is a guard against accidents, not a boundary. A 2FA key never enters the machine:
  `boxline-otp SHOP` in the shell asks the platform for the current code.
- `credentials.update(name, ...)` changes the fields you pass; a new site needs the sensitive values again in the same
  call, and so does a change of where the codes come from (`code_source`, `code_url`: the `password` again).
- `run_script(code, credentials=[...])` lets the script's `step()` calls use credentials (the values never enter the
  machine). In a session with Chrome extensions, steps, scripts and `type_credential` need
  `allow_with_extensions=True`, as agent runs with variables do.
- Errors: `CredentialExistsError` (use `update`), `CredentialNotAllowedError` (the scope does not allow that use),
  `NotFoundError` (`credential_not_found`), `TooManyCredentialValuesError` (the session hides as many values as it can:
  start a new one), `FeatureNotInPlanError` (a password needs the plan's `loginDetails`), `MachineTooOldError` (during
  a deploy), `PlanLimitError` (beyond the plan's `maxCredentials`), `CredentialCodeTimeoutError` (no code or link came
  in time), `CredentialLinkWrongSiteError` (a sign-in link not on the credential's sites), `CredentialLoginFailedError`
  (`session.login` could not sign in; `run_id` is the run that tried), `CodeUrlNotAllowedError` (a `code_url` that is not
  a public HTTPS address).
- The `credential.changed` webhook event says a credential was created, changed (or linked to a profile) or deleted,
  never a value.

### Codes sent by email or SMS, and signing in in one call

A password's 2FA codes can come from an authenticator key (`code_source="totp"`, what `totp_secret` alone means), from
**your system** (`"push"`) or from **an endpoint of yours** (`"url"`). The site's email or SMS goes to you; the AI
never sees the code or a sign-in ("magic") link.

```python
bx.credentials.create(
    "SHOP", "password", origins=["https://shop.example.com"], username="ops@example.com",
    password=os.environ["SHOP_PASSWORD"],
    code_source="push",              # or "url" with code_url="https://ops.example.com/boxline-codes"
    code_timeout_seconds=300,        # how long a run waits for a code (5 to 900)
)

# A run, an action or `boxline-otp SHOP` that needs a code waits for a fresh one. The webhook credential.code_needed
# ({credential, type: "code" | "link", sessionId, runId}) says when; then push what the site sent:
bx.credentials.push_code("SHOP", code="482913")                                      # a 2FA code
bx.credentials.push_code("SHOP", link="https://shop.example.com/magic?t=…")          # or a sign-in link

# Sign in in one call: a short browser-only run with this one credential (default: the session's profile's).
page = s.login("SHOP", url="https://shop.example.com/login")                          # {url, title, runId}
```

- A pushed code or link is used once, by a wait that began before it arrived, and kept sealed for 10 minutes. A link must
  be on one of the credential's sites. `push_code` is not retried by the SDK (a second push is a second code).
- With `code_source="url"` the platform asks your `code_url` every 5 s while a run waits (a signed POST; check it with
  `verify_webhook` and the `codeUrlSecret` that `create` and `update` return once; `credentials.rotate_code_url_secret(name)`
  makes a new one). Answer `{"code": ...}` or `{"link": ...}`, or 204 for "not yet".
- Without a code in time a step, action or `login` raises `CredentialCodeTimeoutError`.

## A script with useModel

`run_script` runs Playwright (JavaScript) code inside the session (it needs `shell=True`). `page`, `context`,
`browser`, `env`, `require`, and the plain-English helpers `step()`, `extract()` and `useModel()` are in scope.

```python
code = """
useModel("claude-haiku-4-5");                  // every later step() and extract() uses it
await page.goto("https://example.com/signup");
await step("type %email% into the email field");
await step("click Continue", { model: "claude-sonnet-5" }); // this call only
console.log(JSON.stringify(await extract("the plan names and prices")));
"""
with bx.sessions.create(shell=True) as s, s.run_script(code, env={"email": "ada@example.com"}) as proc:
    for stream, text in proc:
        print(text, end="")
print(proc.result["exitCode"])
```

## CAPTCHAs

By default (`captcha="ask"`) the platform notices a CAPTCHA that waits for a person and hands over: agent runs and
steps pause, `session.data["attention"]` says which one, and a person solves it in the live view. `"ignore"` carries
on; `"solve"` (paid plans) tries to solve it first. Only automate sites you are allowed to.

```python
with bx.sessions.create(captcha="ask") as s:
    s.goto("https://example.com/signup")
    if s.refresh().data["attention"]:
        print("a person is needed:", s.live_url)
        s.wait_for_human(timeout=300)   # raises CaptchaTimeoutError if nobody solves it
```

A plain-English step that waits too long raises `CaptchaTimeoutError` (409 `captcha_timeout`).

## Browser settings and extensions

Ad and tracker blocking and cookie-banner answers are on every plan; set them when a session starts, or change them
while it runs:

```python
s = bx.sessions.create(block_ads=True, cookie_banners="reject")  # "reject" is the default; "off" leaves banners alone
s.goto("https://example.com")
s.refresh()
print(s.blocked_requests)  # refused inside the machine, before any proxy (collected about every 30 s)
s.update(block_ads=False, cookie_banners="off")
bx.fetch("https://example.com", block_ads=True)  # screenshot, pdf, extract, crawl.start and agent.run take it too
```

Chrome extensions (Manifest V3, plan feature `extensions`): upload the zip of the extension's folder once, then start
sessions with it (at most 10, at start only).

```python
ext = bx.extensions.upload("my-extension.zip")  # a path, or the zip's bytes; at most 10 MB
s = bx.sessions.create(extensions=[ext["id"]])  # loaded before the session is returned
for e in bx.extensions.list():
    print(e["id"], e["name"], e["version"], e["permissions"])
bx.extensions.delete(ext["id"])
```

- **An extension sees every page and every value typed in the session** (agent variables such as passwords too),
  and can send them anywhere. Upload only extensions you trust, and keep secrets in sessions without extensions. An
  agent run with `variables` in a session with extensions raises `VariablesWithExtensionsError` unless you pass
  `allow_with_extensions=True`.
- The upload is checked before it is stored: `InvalidExtensionError` says why (not a zip, Manifest V2, a native
  binary, a `debugger` or `nativeMessaging` permission…), `PayloadTooLargeError` over 10 MB, `LimitReachedError`
  beyond 100 extensions, `ExtensionDeniedError` for one the operator blocks. Uploads carry an `Idempotency-Key`, so a
  retry never stores one twice.

## Webhooks

Signed HTTPS callbacks when a session ends, an agent run, task run or crawl finishes, or a CAPTCHA waits for a person.

```python
endpoint = bx.webhooks.create("https://example.com/webhooks/boxline", ["session.ended", "agent_run.finished"])
# endpoint["secret"] ("whsec_…") is shown only now: store it with your other secrets.
```

In your receiver, verify every delivery against the **raw** body, and drop events you have handled already (a
delivery can arrive more than once):

```python
from boxline import verify_webhook, WebhookSignatureError

# body: the raw bytes exactly as received (request.body in Django, request.get_data() in Flask); re-serialised
# JSON will not match.
try:
    event = verify_webhook(body, request.headers.get("Boxline-Signature"), os.environ["BOXLINE_WEBHOOK_SECRET"])
except WebhookSignatureError as e:
    return Response(e.reason, status=400)  # malformed, timestamp_out_of_range, no_matching_signature
if not already_handled(event["id"]):  # dedupe by the id in the signed body
    handle(event)
return Response(status=200)
```

- The signature's timestamp must be within 5 minutes of your clock (`tolerance_seconds=` changes it), which stops
  replays of old deliveries.
- `bx.webhooks.rotate_secret(id)` returns a new secret; for 24 hours deliveries are signed with both, so switch at your
  pace. `verify_webhook` takes a list too: `[new_secret, old_secret]`.
- `bx.webhooks.test(id)` sends a `webhook.test` event now; `bx.webhooks.test(id, type="agent_run.waiting")` sends a
  made-up sample of that type (marked `test: True`). `bx.webhooks.deliveries(id, status="failed")` lists
  deliveries (newest first) with each attempt's status and `errorCode`; `retry_delivery(id, delivery_id)` sends one
  again.
- `bx.webhooks.event_types()` lists every event type with a description; `events=["*"]` subscribes to all of them, also
  types added later, so ignore types you do not know. `boxline.types.WebhookEventPayload` is the union of every
  event's shape, keyed by `type`.
- `webhook_signature_header(secret, body)` signs a body the way the API does, for your receiver's tests.
- Endpoints must be public HTTPS (`WebhookUrlNotAllowedError`); a plan has `webhookEndpoints` of them.

## Async

`AsyncBoxline` has the same methods; await them, and use `async for` / `async with`:

```python
import asyncio
from boxline import AsyncBoxline

async def main():
    async with AsyncBoxline() as bx:
        pages = await asyncio.gather(*(bx.fetch(u) for u in ["https://example.com", "https://example.org"]))
        async with await bx.sessions.create() as s:
            print((await s.goto("https://example.com"))["title"])
        async for session in bx.sessions.list(status="RUNNING"):
            print(session.id)

asyncio.run(main())
```

## Lists and pages

Every list takes `limit` and `after`. A list call returns its first page; iterating it yields every item (the SDK
follows `next`):

```python
page = bx.sessions.list(status=["RUNNING", "PAUSED"], limit=50)
print(len(page.data), page.total, page.next)

for s in bx.sessions.list(status="RUNNING"):
    print(s.id)
for e in session.events(types=["console", "error"]):
    print(e.get("text"))
for p in page.iter_pages():
    print(len(p.data))
```

With `AsyncBoxline`, `await bx.sessions.list()` is the first page and `async for s in bx.sessions.list()` goes through
all of them. Lists: `sessions.list`, `sessions.events`, `sessions.pages`, `profiles.list`, `api_keys.list`,
`agent.list`, `crawl.list`, `extensions.list`, `tasks.list`, `tasks.runs`, `credentials.list`, `credentials.audit`, and a crawl's pages (`crawl.get(id, after=…)`,
or `for p in bx.crawl.pages(id)`).

## Errors, retries and time limits

- **Typed errors.** Every failure is a `BoxlineError` with `status`, `code` (stable, snake_case), `message`,
  `request_id` (the API's id: quote it when asking for support) and `client_request_id` (yours, if you sent one).
  Subclasses: `RateLimitError` (`retry_after`), `FeatureNotInPlanError`, `ProjectSuspendedError`,
  `IdempotencyMismatchError`, `IdempotencyInProgressError`, `InvalidCursorError`, `CaptchaTimeoutError`,
  `PageUnreachableError`, `PageTimeoutError`, `ModelRefusedError` (extract), `SearchUnavailableError`,
  `OutOfViewportError`, `WebhookUrlNotAllowedError`, `WebhooksUnavailableError`, `WebhookDisabledError`,
  `PayloadExpiredError`, `WebhookSignatureError` (from verify_webhook), `VariablesWithExtensionsError`,
  `InvalidExtensionError`, `PayloadTooLargeError`, `LimitReachedError`, `ExtensionDeniedError`, `CrossSiteRequestError`,
  `MissingVariablesError`, `PlanLimitError`, `CredentialExistsError`, `CredentialNotAllowedError`, `TooManyCredentialValuesError`,
  `CredentialCodeTimeoutError`, `CredentialLinkWrongSiteError`, `CredentialLoginFailedError`, `CodeUrlNotAllowedError`,
  `MachineTooOldError`, `NotContinuableError`, `TooManyMessagesError`, `SessionNotRunningError`, `AuthenticationError`,
  `NotFoundError`, and
  `BoxlineConnectionError` /
  `BoxlineTimeoutError` when no answer came back. `ErrorCode` has the codes.
- **Retries.** GETs, and the calls that create or start something (sessions, bulk, agent runs, continued runs, messages
  to runs, crawls, API keys, profiles, extension uploads, tasks, task runs), are retried after a network error, a time-out, 429 and 5xx: 2 retries by default, exponential backoff
  from 0.5 s to 8 s with jitter, or what `Retry-After` / `RateLimit-Reset` say (up to 60 s; longer waits go to you as a
  `RateLimitError`). Other POSTs (exec, actions, fetch…) are never retried: they could run twice.
- **Idempotency keys.** The SDK sends a new `Idempotency-Key` with every create, and the same one on its retries, so a
  retry never starts a second session or run. Pass your own to make your own retries safe:
  `bx.sessions.create(options={"idempotency_key": "job-42"})`. The same key with another body raises
  `IdempotencyMismatchError` (for a new API key: also when another caller used it).
- **Time limits.** `timeout` in seconds per request (default 120); long calls (exec, `files.wait_for`, plain-English
  steps) get the time they need.

```python
bx = Boxline(max_retries=3, timeout=60)
bx.sessions.get(session_id, options={"timeout": 5, "max_retries": 0, "client_request_id": "trace-42"})
patient = bx.with_options(max_retries=6)
```

Every request carries `Boxline-SDK: python/<version>`.

## Every method

Every public API operation has a method, with the Node SDK's names in snake_case (the full list is
`docs/sdk-methods.json` in the platform repository). Session methods take the id first; a `Session` has the same
methods without it (`session.pause()`).

| Area | Methods |
|---|---|
| Account | `me`, `has_feature`, `auth.signup`, `auth.login`, `auth.logout`, `project.trajectories`, `project.set_trajectories`, `project.settings`, `project.set_settings`, `api_keys.list`, `api_keys.create`, `api_keys.revoke` |
| Webhooks | `webhooks.create`, `list`, `get`, `update`, `delete`, `rotate_secret`, `test`, `deliveries`, `retry_delivery`; `verify_webhook` (no request) |
| Sessions | `sessions.create`, `get`, `list`, `update`, `release`, `pause`, `resume`, `move`, `extend`, `rotate_proxy`, `rotate_urls`, `live`, `bulk` |
| Browser | `sessions.actions`, `sessions.computer`; on a session: `goto`, `click`, `hover`, `fill`, `type`, `type_credential`, `press`, `scroll`, `wait`, `select`, `elements`, `evaluate`, `content`, `screenshot`, `cursor`, `upload`, `tabs`, `new_tab`, `switch_tab`, `close_tab`, `back`, `forward`, `reload`, `step`, `extract`, `export_cookies`, `computer`, `mouse.move`/`move_by`/`click`/`down`/`up`/`drag`, `keyboard.key`/`type`/`press` |
| Shell and scripts | `sessions.exec`, `exec_stream`, `run_script`, `restart_shell` |
| Files | `sessions.files.list`, `read`, `read_text`, `write`, `delete`, `wait_for` |
| Logs | `sessions.events`, `stream_events`, `pages`, `recording`, `recording_frame`; on a session: `wait_for_human` |
| Browser profiles | `profiles.create`, `get`, `list`, `update`, `delete` |
| Credentials | `credentials.create`, `list`, `get`, `update`, `delete`, `audit`, `push_code`, `rotate_code_url_secret`; on a session: `type_credential`, `login` |
| Extensions | `extensions.upload`, `list`, `get`, `delete` |
| Web | `fetch`, `screenshot`, `pdf`, `extract`, `search`, `crawl.start`, `get`, `list`, `cancel`, `pages`, `wait` |
| Agent | `agent.models`, `run`, `get`, `list`, `takeover`, `hand_back`, `cancel`, `continue_run`, `send_message`, `stream`, `wait` |
| Tasks | `tasks.create`, `list`, `get`, `update`, `delete`, `run`, `runs`, `wait_for_run` |
| Usage | `usage`, `stats`, `pricing`, `openapi`, `health` |

## Examples

In `examples/`. Most use the small example site (`python examples/test_site.py`), which only a local API's sessions can
reach, so run them with `BOXLINE_API_URL=http://localhost:8080`; `search.py` and `quickstart.py` work anywhere.

| File | Shows |
|---|---|
| `quickstart.py` | a session, Playwright over CDP, the actions API |
| `mixed_download_process_fill.py` | Playwright download → Python in the shell → files API → a form filled with actions |
| `agent_mixed.py` | one agent run using the browser and the shell, streamed step by step |
| `script_mixed.py` | a script mixing Playwright, `child_process`, `fs`, `useModel` and `step()` |
| `async_quickstart.py` | `AsyncBoxline`, `asyncio.gather`, `async for` |
| `page_apis.py` | fetch, screenshot, pdf, crawl, extract, a page that cannot load |
| `search.py` | web search, with the top pages fetched as Markdown, and the cache |
| `computer_use.py` | mouse and keyboard (drag, hover, double-click, keys), each action's `text`, `session.computer()` in both shapes |
| `agent_computer.py` | an agent run in computer mode, with its thoughts streamed |
| `webhook_receiver.py` | a receiver (standard library only) that verifies each delivery and drops duplicates |
| `webhooks.py` | endpoints, deliveries and their history, re-sending, rotating the secret, a test event |
| `agent_variables.py` | an agent run with `%email%` / `%password%` limited to one site |
| `tasks.py` | a task with an output schema on a demo shop: run with a variable, waited for, its history, changed, deleted; an agent run with `output` |
| `credentials.py` | a secret exported into a shell (its length checked, its value hidden in output), changed, audited, deleted; a password with 2FA linked to a profile |
| `script_use_model.py` | `useModel`, `step()` and `extract()` in a script |
| `captcha.py` | noticing a CAPTCHA and handing it to a person |
| `block_ads.py` | a session that blocks ads and trackers, and its `blocked_requests` count |
| `extension.py` | a tiny MV3 extension built with `zipfile`, uploaded, loaded into a session, and deleted |
| `list_sessions.py` | cursor pages and iteration |
| `reliability.py` | idempotency keys, request ids, typed errors, time limits |

## Developing the SDK

`src/boxline/_async_client.py` is the source of both clients; `_client.py` is generated from it:

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"   # the distribution is boxline-sdk
python scripts/unasync.py          # after editing _async_client.py
.venv/bin/python -m pytest         # unit tests (fake HTTP), coverage of docs/openapi.yaml, the generated file
```

---

This repository holds the Boxline Python SDK (boxline-sdk). It is copied from Boxline's main repository on every change. Issues and pull requests are welcome here; accepted changes are made there and arrive with the next copy.
