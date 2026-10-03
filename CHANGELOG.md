# Changelog

All notable changes to `boxline-sdk`, the Python SDK (imported as `boxline`). It follows
[semantic versioning](https://semver.org).

## 1.3.0 (2026-10-03)

### Added

- **Time limits that fit the server.** `session.login`, `type_credential` with `field="otp"` and plain-English steps that use `%NAME.otp%` or
  `%NAME.link%` wait as long as the server may (up to 24 minutes for `login`, 16 for a code), instead of the
  default 120 s that cut off a login waiting for a pushed code.

- **Codes from your own system.** A password credential's `codeSource` is `"totp"` (the authenticator key,
  `totp_secret`; `totp_secret` alone still means this), `"push"` or `"url"`, or None for no 2FA; with `"push"` or
  `"url"` a run, action or `boxline-otp` that needs a code (or a sign-in link) waits for a fresh one, up to
  `codeTimeoutSeconds` (5 to 900, default 300). `credentials.create` and `credentials.update` take `code_source`,
  `code_url` (a public HTTPS endpoint the platform asks with a signed POST) and `code_timeout_seconds`; a
  `PasswordCredential` shows `codeSource`, `codeUrl` and `codeTimeoutSeconds`. Changing `code_source` or `code_url` needs
  the `password` again (400 `invalid_request`, 409 `conflict` on a race). With a `code_url` the answer has `codeUrlSecret`
  once (`types.PasswordCredentialWritten`, `types.CredentialWritten`). Types `CredentialCodeSource`.
- **`credentials.push_code(name, code=...)` / `push_code(name, link=...)`**: sends the code or sign-in link the site
  emailed or texted to a wait in progress (used once, kept sealed for 10 minutes; a link must be on one of the
  credential's sites). Types `CredentialCodeAccepted`. Not retried.
- **`credentials.rotate_code_url_secret(name)`**: a new `codeUrlSecret` for the requests to `code_url` (check them with
  `verify_webhook`).
- **`session.login(credential=None, url=None, allow_with_extensions=None)`** and the `login` action: signs the browser
  in with a password credential in one call (a short browser-only agent run with that one credential and its sites).
  Returns `{url, title, runId}` (`types.LoginValue`); an `ActionResult` of a failed `login` has `runId` and a `code`.
- Errors `CredentialCodeTimeoutError` (`credential_code_timeout`: no code or link in time),
  `CredentialLinkWrongSiteError` (`credential_link_wrong_site`), `CredentialLoginFailedError` (`credential_login_failed`,
  with `run_id`), `CredentialLoginTimeoutError` (`credential_login_timeout`: the login ran out of time, 15 steps plus the
  credential's `codeTimeoutSeconds` for a pushed or asked code, and its run was canceled; a kind of
  `CredentialLoginFailedError`) and `CodeUrlNotAllowedError` (`code_url_not_allowed`), and the matching `ErrorCode` values.
- `session.login` returns the page's origin only (`url`) when the page is the one a sign-in link opened, since such a link
  can keep its token in the path.
- The webhook event `credential.code_needed` (`types.WebhookCredentialCodeNeededData`: `credential`, `type`, `sessionId`,
  `runId`): a run waits for a code or link, so forward the site's email or SMS now. Never a code or link.
- The credentials audit has `action: "code"` for a pushed code or link (never its value).
- An agent run's steps (and `agent.stream` events) have `type: "code"` while it waits for a code or link: `state`
  `"waiting"` (push it now), then `"received"` or `"timeout"`, with `credential` and `kind`; never the value.

## 1.2.0 (2026-10-03)

### Added

- **Credentials**: `bx.credentials.list/create/get/update/delete/audit` (sync and async; `/v1/credentials`), one place
  for what the AI types and the shell uses, with a typed union per type: a **password** (`origins`, `username`,
  `password`, optional `totp_secret`; `types.PasswordCredential` with `username` and `hasTotp`) and a **secret**
  (`value`, optional `origins`; `types.SecretCredential` with `preview`). `credentials.create(name, "password", ...)` and
  `credentials.create(name, "secret", ...)` are typed per type; `scope`, `shell`, `description` and the write-only
  rules are as for secrets. Types `Credential`, `CredentialType`, `CredentialScope`, `CredentialField`,
  `CredentialAuditEntry` (with `type`, and the new `usedBy` kinds `action` and `otp`) and `CredentialUse`. Passwords need
  the plan's `loginDetails`; the plan's limit is `maxCredentials`.
- `credentials.update` that makes an AI-only credential readable by shells (`scope` to `"shell"` or `"all"`, or
  `shell=True`) needs the values again in the same call, like a new site (the API answers 400 `invalid_request`
  otherwise, 409 `conflict` when the sites, scope or `shell` changed meanwhile).
- **`credentials=["NAME"]`** where the API takes them: `sessions.create` (exported into the shell: a secret as `$NAME`,
  a password as `$NAME_USERNAME` and `$NAME_PASSWORD`; `boxline-otp NAME` prints its 2FA code), `exec` and `exec_stream`,
  `agent.run`, `session.step()` and the step action (placeholders `%NAME%`, `%NAME.username%`, `%NAME.password%`,
  `%NAME.otp%`), `run_script`, and `tasks.create` / `tasks.update` (a task shows its `credentials`). A `Session` lists
  the exported `credentials`.
- **`session.type_credential(name, field, selector, allow_with_extensions)`** and the `type` action with `credential`
  (and `field`: `"username"`, `"password"` or `"otp"`): a credential's value typed into a field without passing
  through your code, only on the credential's sites.
- **`profiles.update(profile_id, name=None, credential=...)`**: rename a profile and/or link the password credential it
  signs in with (`credential=None` unlinks it). A `Profile` has `credential`; sessions with the profile, and the agent
  runs, task runs and steps in them, get that credential as if it were listed.
- Errors `CredentialExistsError` (409 `credential_exists`), `CredentialNotAllowedError` (400 `credential_not_for_ai` /
  `credential_not_for_shell`), `TooManyCredentialValuesError` (409 `too_many_credential_values`) and
  `ErrorCode.CREDENTIAL_NOT_FOUND` (404 `credential_not_found`, a `NotFoundError`). The webhook event
  `credential.changed` (`WebhookCredentialChangedData`: `name`, `action`, `type`, `by`, `changed`).
- `examples/credentials.py`.

### Changed (breaking)

Nothing above shipped before, so these old names are gone with no aliases.

- **Secrets and profile login details became credentials.** `bx.secrets` is `bx.credentials` (`Secrets` and
  `AsyncSecrets` are `Credentials` and `AsyncCredentials`); `secrets=` on `sessions.create`, `exec`, `exec_stream`,
  `agent.run`, `step`, `run_script` and `tasks.create` / `tasks.update` is `credentials=` (`Session.secrets` is
  `Session.credentials`, `Task.secrets` is `Task.credentials`); `login=True` on `run_script` is gone (list the
  credential); `profiles.set_login`, `update_login` and `delete_login`, `Profile.login`, `ProfileLogin` and the
  placeholders `%login.username%`, `%login.password%` and `%login.otp%` are gone: create a password credential and link
  it with `profiles.update(id, credential=...)`. `credentials.create` takes `(name, type, ...)` with keyword arguments
  per type. `types.Secret`, `SecretScope`, `SecretUse` and `SecretAuditEntry` are replaced by the credential types above;
  `SecretExistsError`, `SecretNotAllowedError` and `TooManySecretValuesError` by `CredentialExistsError`,
  `CredentialNotAllowedError` and `TooManyCredentialValuesError`; the plan field `maxSecrets` is `maxCredentials`; the
  webhook event `secret.changed` is `credential.changed`.
- **Saved logins (contexts) are now browser profiles.** `bx.contexts` is `bx.profiles` (`create`, `get`, `list`,
  `update`, `delete`; sync and async; the routes are `/v1/profiles`; `contexts.rename` is `profiles.update(id, name=...)`).
  `sessions.create` and `agent.run` take `profile=` (and `persist_profile=` on `sessions.create`) instead of
  `context=` and `persist_context=`; `tasks.create` and `tasks.update` take `profile=` instead of `saved_login=`; the
  methods' first argument is `profile_id`. A `Session` has `profileId` and `profilePersist` instead of `contextId`
  and `contextPersist`; the plan fields are `maxProfiles` and `maxProfileBytes`, the plan feature is `profiles`, and
  the error code is `ErrorCode.PROFILE_TOO_LARGE` (`profile_too_large`). Types: `types.Profile` (was `Context`) and
  `TaskProfile`; the classes `Contexts` and `AsyncContexts` are `Profiles` and `AsyncProfiles`. New profiles have ids
  starting with `prof_`. The old names are gone.

## 1.1.0 (2026-10-02)

### Added

- **Partial results from a several-page extract**: a page that did not load (`page_unreachable`, `page_timeout`) or is
  not a web page is listed in `pages` with `status: None`, `finalUrl: None` and `error: {code, message}`, and the
  call fails only when none load.
- **`NotAWebPageError`** (422 `not_a_web_page`, not retried): `fetch` or `extract` of an address that answers with a
  PDF or another document Chrome only displays.
- **Streamed exec**: `exec_stream` skips the API's `waiting` and `ping` lines and raises an `error` line as the error
  it names; `exec` allows up to 10 minutes of session setup (`SETUP_WAIT_S`) before the command's own time limit.
- **Own model keys and a default model** (sync and async): `bx.project.model_keys()`, `set_model_key(provider, key=, use=)` and
  `delete_model_key(provider)` (a key is write-only: `preview` is its last 4 characters); `default_model=` on
  `project.set_settings()`; `keySource` on agent runs, extract results and `agent.models()` providers; `Provider` now also
  `"xai"` (Grok) and `"google"` (Gemini); the plan feature `platformModels` (off on Free) and `ownKeyModelCostUsd` in stats.
- **Save a sign-in from a working session** (sync and async): `bx.contexts.create(name=, from_session=, attach=)` makes a
  saved login from the session's current cookies and site storage; `attach=True` also makes the session save to it from
  now on. `ErrorCode.CONTEXT_TOO_LARGE` (413 over 16 MB); `PlanLimitError` past the plan's `maxContexts` or
  `maxContextBytes` (new `Plan` fields).
- **Runs whose server stopped**: the run errorCode `server_restarted` (continuable: `agent.continue_run` goes on in the
  same browser; `ErrorCode.SERVER_RESTARTED`, in `AgentRunErrorCode`), and `RunNotLiveError` (409 `run_not_live`,
  `ErrorCode.RUN_NOT_LIVE`) from `agent.takeover`, `hand_back` and `send_message` on such a run.
- **Webhook events for everything a receiver may need** (sync and async): `types.WebhookEventType` has
  `session.started`, `session.expiring`, `agent_run.started`, `agent_run.waiting`, `agent_run.resumed`, `captcha.solved`,
  `captcha.failed`, `task_run.started`, `task.schedule_paused`, `usage.limit_reached`, `api_key.created`,
  `api_key.revoked`, `secret.changed`, `webhook.changed`, `extension.uploaded` and `extension.deleted`. `events=["*"]`
  subscribes to every type (`types.WebhookSubscription`). Typed payloads: `types.WebhookEventPayload` (a union of
  TypedDicts keyed by `type`) and one TypedDict per `data` shape; `WebhookEvent` has `test`. `bx.webhooks.event_types()`
  (`GET /v1/webhooks/events`); `bx.webhooks.test(id, type=)` sends a sample of any type. `verify_webhook` is unchanged.
- Agent runs carry `variableNames` (the names of the run's own variables, never values): what `continue_run` needs
  again.

- **Continue a run that stopped at a limit** (sync and async): `bx.agent.continue_run(run_id, max_steps=,
  max_cost_usd=, instruction=, variables=)` returns the new run (same session; `continuedFrom`), which works with `wait`
  and `stream` like any run. Runs carry `continuable` (`{"until"}` or None), `continuedFrom`, `continuedBy`,
  `sessionExpiresAt`, `maxSteps`, `maxCostUsd` and `maxConsecutiveErrors`; the stream's `done` event carries
  `continuable`. `NotContinuableError` (409 `not_continuable`), `SessionNotRunningError`; types `Continuable`,
  `AgentRunErrorCode`, `SessionEndReason`.
- **Messages to a working run**: `bx.agent.send_message(run_id, text)` (`AgentMessageSent`); delivered messages are
  `message` steps in the run and its stream. `TooManyMessagesError` after 50.
- **Run limits** on `agent.run`: `max_cost_usd`, `max_consecutive_errors`, and `timeout` / `idle_timeout` for the run's
  own session. `max_steps` takes 1-1000; `max_steps=None` now means no step limit (not passing it keeps the default of
  30). New `ErrorCode` values `MAX_STEPS`, `MAX_COST`, `TOO_MANY_ERRORS`, `NO_PROGRESS`, `SESSION_TIMEOUT`,
  `SESSION_ENDED`, `NOT_CONTINUABLE`, `TOO_MANY_MESSAGES`, `SESSION_NOT_RUNNING`.
- **Idle timeout**: `idle_timeout` on `sessions.create`, `sessions.update` and `Session.update` (`None` switches it off
  on update); sessions carry `idleTimeout`, and `endReason` can be `"idle"`.
- Tasks: `max_steps=None` (no step limit) on `tasks.create` and `tasks.update`, `max_cost_usd`, and `timeout` /
  `idleTimeout` in `browser`.
- `IDEMPOTENT_POSTS` has `/v1/agent/runs/:id/continue` and `/v1/agent/runs/:id/messages`: both send an Idempotency-Key
  and are retried like the other creates.
- **Tasks** (saved agent runs), in `Boxline` and `AsyncBoxline`: `bx.tasks.create/list/get/update/delete/run/runs`,
  with `%name%` variables (plain, or `"secret": True` with `origins` and `shell`), `secrets` (project secret names,
  usable by scheduled tasks too), an `output` schema, `browser` settings, `saved_login`, `model`, `max_steps` and a `schedule` (`cron`, `timezone`, `variables`, `enabled`;
  `nextRunAt` and `nextRuns`, the next 3 times in UTC, on the schedule; `lastRun`, the newest run, on the task; `None` removes a field on update, schedule fields are merged). `tasks.runs(task_id,
  status=…)` pages a task's run history by cursor; a run's status can be `queued` (a scheduled run waiting for its
  turn), `skipped` and `missed` too. `tasks.wait_for_run(run | task_id, task_run_id, poll=1.0, timeout=1800.0)`
  waits for a task run's result. Types `Task`, `TaskRun`, `TaskRunStatus`, `TaskVariable`, `TaskSchedule`,
  `TaskSavedLogin` in `boxline.types`.
- **Structured output**: `output=` (a JSON Schema, `boxline.types.OutputSchema`) on `agent.run`. `AgentRun.result` is
  typed `Any` (the JSON answer with a schema, else the text); runs carry `resultText`, `output`, `errorCode`
  (`ErrorCode.OUTPUT_INVALID`), `taskId` and `taskRunId`; the stream's `done` event carries `resultText` and
  `errorCode`.
- `MissingVariablesError` (400 `missing_variables`) and `PlanLimitError` (402 `plan_limit`).
- Saving a task and starting a task run send an Idempotency-Key and are retried like the other creates:
  `IDEMPOTENT_POSTS` has `/v1/tasks` and `/v1/tasks/:id/runs` (`:id` is one path segment; `is_idempotent_post(route)`).
- `examples/tasks.py`.
- **Project secrets** (write-only), in `Boxline` and `AsyncBoxline`: `bx.secrets.list/create/get/update/delete/audit`,
  with `scope` (`"agent"`, `"shell"`, `"all"`), `origins`, `shell` and `description` (`None` clears it on update,
  `origins=None` allows any site); `preview` shows the last 4 characters of long values, never more.
  `secrets.audit(name=…)` pages the changes and uses. Types `Secret`, `SecretScope`, `SecretAuditEntry` in
  `boxline.types`. Creating a secret is not retried (the API takes no Idempotency-Key there).
- **Saved login details with 2FA**: `bx.contexts.set_login(context_id, origin, username, password, totp_secret=None)`
  (a full replace), `contexts.update_login(context_id, origin=, username=, password=, totp_secret=)` (keeps what is not
  passed; `totp_secret=None` removes 2FA; a new `origin` needs `password`, and `totp_secret` when the login has 2FA) and
  `contexts.delete_login(context_id)`; contexts carry `login` (never the password or the 2FA secret);
  `loginDetails` in plan features.
- **Shell environment and secrets**: `env=` and `secrets=` on `sessions.create` (sessions show the names in `env` and
  `secrets`), `secrets=` on `exec` and `exec_stream` for one command; `sessions.move` also returns `shell` (the
  directory, the exported variables that came along, the processes that were stopped).
- **Secrets for the AI**: `secrets=` on `agent.run` (and `context=`, a saved login for the run's own session), on
  `session.step()`, and on `run_script` with `login=` (the saved login's details for `step()`);
  `allow_with_extensions=` on `step` and `run_script`, as on `agent.run`.
- `TooManySecretValuesError` (409 `too_many_secret_values`), `SecretExistsError` (409 `secret_exists`),
  `SecretNotAllowedError` (400 `secret_not_for_ai` / `secret_not_for_shell`), `MachineTooOldError` (409
  `machine_too_old`); `PlanLimitError` also for secrets beyond `maxSecrets`. `plan_limit` is always 402 (a session longer than the plan allows was 403).
- Plans carry `tasks`, `schedules` and `maxSecrets`.
- `examples/secrets.py`.
- `emailVerified` and `termsCurrentVersion` on `me()["user"]`; `"account_recovered"` as a webhook endpoint's
  `disabledReason` and a session's `endReason` (a password reset recovered an account whose email was not confirmed).

## 1.0.0 (not published yet)

The first stable release: every public API operation has a method, calls are retried safely, and there is an async
client.

### Added

- **Web search**: `bx.search(query, limit, country, language, recency, safe_search, fetch, proxy)`, with the top pages
  as Markdown when `fetch` is set; `SearchUnavailableError`; `webSearch` in plan features, `searchesPerMonth` and
  `extraSearchesPer1000Usd` on plans, `searches` in `usage()`, `webSearch` in `pricing()`.
- **Mouse, keyboard and computer use**: `session.mouse.move/move_by/click/down/up/drag` (points as `(x, y)`, dicts or
  selectors), `session.hover`, `session.keyboard.key/type/press`, `session.cursor()`; `click` takes `button`, `count`
  and `modifiers`, `scroll` takes `delta_x` and `modifiers`, `screenshot` takes `max_width` and `cursor`; bare strings in
  action lists are plain-English steps; results carry `text`. `session.computer(action, max_width, screenshot, format,
  quality, cursor)` / `bx.sessions.computer(session_id, …)` runs one Anthropic- or OpenAI-shaped computer-use action
  and returns the screen; `OutOfViewportError`.
- **Agent**: `agent.run(task, mode="computer")`, `mode` on runs, `thought` on steps and the `thought` stream event,
  `supportsComputerUse` and `computerTool` in `agent.models()`.
- `ModelRefusedError` (422 `model_refused`, extract).
- **Webhooks**: `bx.webhooks.create/list/get/update/delete/rotate_secret/test/deliveries/retry_delivery` (deliveries
  page by cursor, filter by `status`, and carry `errorCode` and their attempt `history`; endpoints carry
  `pausedUntil`), and `verify_webhook(body, header, secret | secrets, tolerance_seconds=300)`, which accepts exactly
  what the API's signer makes (checked against its vectors, JavaScript's trimming and length rules included) and raises
  `WebhookSignatureError` (`reason`); `webhook_signature_header()` for tests. `WebhookUrlNotAllowedError`,
  `WebhooksUnavailableError`, `WebhookDisabledError`, `PayloadExpiredError`, and the `queue_full` code;
  `webhookEndpoints` on plans.
- **Project settings**: `bx.project.settings()` and `set_settings(captcha_default=…)`.
- **Accounts and retention**: `auth.signup(…, name=…)`; `name` on users, `termsVersion` and `termsUpdate` on
  `me()["user"]`; `retentionDays` on plans (how long an ended session's recording, logs and run steps are kept) and
  `dataDeletedAt` on sessions (when they were deleted).
- **Browser settings**: `block_ads` and `cookie_banners` (`"reject"` | `"off"`) on `sessions.create`, `update`
  (and `Session.update`) and `agent.run`; `block_ads` on `fetch`, `screenshot`, `pdf`, `extract` and `crawl.start`;
  sessions carry `blockAds`, `blockedRequests`, `cookieBanners` and `extensions` (`s.blocked_requests`…).
- **Chrome extensions**: `bx.extensions.upload(zip)` (bytes or a path; raw `application/zip`; with an automatic
  Idempotency-Key), `list`, `get`, `delete`, sync and async; `extensions=[id]` on `sessions.create` and
  `agent.run`; `allow_with_extensions` on `agent.run`; the `Extension` type; `extensions` in plan features.
  `VariablesWithExtensionsError`, `ExtensionDeniedError`, `CrossSiteRequestError`, `InvalidExtensionError`,
  `PayloadTooLargeError` and `LimitReachedError`.
- `Session.actions()` is typed to take bare-string steps too (`ActionItem = dict | str`), like `sessions.actions()`.

- **`AsyncBoxline`** (httpx.AsyncClient) with the same methods as `Boxline`: `await` them, `async for` over lists and
  streams, `async with` for the client and sessions. Both clients come from one source (`_async_client.py`).
- **Every public endpoint.** New: `auth.signup/login/logout`, `api_keys.list/create/revoke`, `sessions.live` (also
  `session.live()`), `sessions.stream_events`, `sessions.recording_frame`, `crawl.pages`, `pricing`, `openapi`,
  `health`. Session methods are also on `bx.sessions` with the id first (`bx.sessions.pause(session_id)`).
- **Browser helpers** on a session: `scroll`, `wait`, `select`, `elements`, `upload`, `tabs`, `new_tab`,
  `switch_tab`, `close_tab`, `back`, `forward`, `reload`; `click` also takes `x`, `y`; `type` takes `delay_ms`.
- **Retries** with exponential backoff (0.5 s to 8 s) and jitter after network errors, time-outs, 429 and 5xx, for
  GETs and for the calls that create or start something; `Retry-After` and `RateLimit-Reset` are honoured.
  `max_retries` (default 2) per client, or `options={"max_retries": …}` per call.
- **Idempotency keys**: a new `Idempotency-Key` per create call, reused by its retries; `options={"idempotency_key":
  …}` for your own.
- **Time limits**: `timeout` per client (seconds, default 120) or `options={"timeout": …}` per call.
- **Typed errors**: `BoxlineError` now has `request_id` (the API's), `client_request_id` (yours), `headers`, `body` and
  `retryable`, with subclasses `RateLimitError`, `FeatureNotInPlanError`, `ProjectSuspendedError`,
  `IdempotencyMismatchError`, `IdempotencyInProgressError`, `InvalidCursorError`, `CaptchaTimeoutError`,
  `PageUnreachableError`, `PageTimeoutError`, `AuthenticationError`, `NotFoundError`, `BoxlineConnectionError`,
  `BoxlineTimeoutError`, and the `ErrorCode` constants.
- **Cursor pages**: every list takes `limit` and `after` and returns a `Page` (`data`, `next`, `has_next_page()`,
  `get_next_page()`, `iter_pages()`); iterating it goes through every page.
- **Types**: TypedDicts for the API's objects in `boxline.types` (`SessionData`, `AgentRun`, `CrawlJob`, `Usage`, …),
  matched to the API description.
- `options={"client_request_id": …}` (sent as `X-Client-Request-Id`), `headers=`, `bx.with_options()`, the
  `Boxline-SDK: python/1.0.0` header; `fetch` takes `links`, `delay_ms` and `viewport`; `pdf` takes `viewport`.
- Runnable examples in `examples/`, including browser + shell + files in one session.
- Dependency: `typing-extensions`.

### Changed (breaking)

- **The package is `boxline-sdk` on PyPI** (`pip install boxline-sdk`; the name `boxline` there is an unrelated
  project). The import name stays `boxline`.
- The default API is `https://api.boxline.dev` (it was `http://localhost:8080`); `BOXLINE_API_URL` and `base_url` still
  choose another one.
- `auth.signup(email, password, accept_terms=True)`: `accept_terms` is required (the user accepts the terms of service
  and acceptable use policy); the API refuses a signup without it (400 `terms_not_accepted`).

- List methods return a `Page` instead of a list: iterate it (every page) or use `.data` (this page). `page["data"]`,
  `page["total"]` and `len(page)` still work as before.
- `contexts.list(limit, after)` and `crawl.get(crawl_id, limit, after)`: the `offset` arguments are gone (use `after`),
  and a crawl's `next` is a cursor string.
- `sessions.list()` takes named filters (`status`, `kind`, `q`, `from_`, `to`, `sort`, `limit`, `after`).
- `session.pages()` returns a `Page` instead of a list.
- A client given its own `http_client` sends the API key per request (before, it was only set on the SDK's own
  client), and `close()` leaves an `http_client` you passed in open.

### Deprecated

- `sessions.page()`: use `sessions.list()`.

## 0.1.0

- The first version: sessions, actions, exec, files, contexts, the agent, crawl and the quick APIs.
