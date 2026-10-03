"""The API's objects as TypedDicts (plain dicts at run time, with the API's camelCase keys), as in
docs/openapi.yaml and docs/CONTRACT.md. Dates are ISO 8601 strings."""

from typing import Any, Dict, List, Optional, Union

from typing_extensions import Literal, NotRequired, TypedDict

SessionStatus = Literal["RUNNING", "PAUSED", "COMPLETED", "ERROR"]
CaptchaMode = Literal["ask", "ignore", "solve"]
CaptchaKind = Literal["recaptcha", "hcaptcha", "turnstile", "cloudflare", "datadome", "arkose", "human"]
WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]
#: Model providers: Anthropic Claude, OpenAI, Grok (xAI) and Google Gemini (Grok and Gemini have no computer use).
Provider = Literal["anthropic", "openai", "xai", "google"]
#: Whose key a model call uses: the project's own ("project": no model charge from Boxline) or the platform's.
KeySource = Literal["project", "platform"]


class Viewport(TypedDict):
    width: int
    height: int


# ---------------------------------------------------------------- accounts and plans


class User(TypedDict):
    id: str
    email: str
    #: The user's name (signup, login and ``me``), or None.
    name: NotRequired[Optional[str]]


class MeUser(User):
    isAdmin: bool
    #: The latest terms of service version the user accepted (a date), or None.
    termsVersion: NotRequired[Optional[str]]
    #: Newer terms exist: the console asks the user to accept them.
    termsUpdate: NotRequired[bool]
    #: The current terms version (what accepting the terms records).
    termsCurrentVersion: NotRequired[str]
    #: The user confirmed the email (a completed password reset).
    emailVerified: NotRequired[bool]


class Suspension(TypedDict):
    at: str
    reason: Optional[str]


class Project(TypedDict):
    id: str
    name: str
    plan: str
    suspended: NotRequired[Optional[Suspension]]


class SignupResponse(TypedDict):
    user: User
    project: Project
    #: The first API key, shown only here.
    apiKey: str


class LoginResponse(TypedDict):
    user: User
    project: Project


class PlanFeatures(TypedDict, total=False):
    shell: bool
    pauseResume: bool
    profiles: bool
    recording: bool
    realisticBrowser: bool
    residentialProxy: bool
    datacenterProxy: bool
    customProxy: bool
    captchaSolving: bool
    agentRuns: bool
    steps: bool
    extract: bool
    quickApis: bool
    crawl: bool
    extensions: bool
    webSearch: bool
    #: Credentials of type "password" (with their 2FA codes).
    loginDetails: bool
    #: Model calls on Boxline's keys; without it (Free) a project runs models on its own keys only.
    platformModels: bool


class Plan(TypedDict):
    """A plan's limits (None = no limit) and features."""

    id: NotRequired[str]
    name: str
    priceUsd: Optional[float]
    includedUsd: float
    concurrency: int
    maxTimeoutSeconds: int
    modelSpendCapUsd: Optional[float]
    proxyGbPerMonth: Optional[float]
    captchaSolvesPerMonth: Optional[int]
    #: Web searches included per calendar month (UTC); None = no limit.
    searchesPerMonth: NotRequired[Optional[int]]
    #: Price per 1,000 searches beyond searchesPerMonth; None = no searches beyond it (402 plan_limit).
    extraSearchesPer1000Usd: NotRequired[Optional[float]]
    #: Webhook endpoints a project may have; None = no limit.
    webhookEndpoints: NotRequired[Optional[int]]
    #: Days an ended session's recording, logs and agent-run steps are kept (then deleted; the session stays).
    retentionDays: NotRequired[int]
    #: Tasks a project may have; None = no plan limit (1,000 per project at most).
    tasks: NotRequired[Optional[int]]
    #: Tasks with a schedule switched on; 0 = no schedules, None = no limit.
    schedules: NotRequired[Optional[int]]
    #: Credentials (passwords and secrets together) a project may keep.
    maxCredentials: NotRequired[int]
    #: Browser profiles a project may keep; None = no limit.
    maxProfiles: NotRequired[Optional[int]]
    #: Bytes a project's profiles may hold together; None = no limit.
    maxProfileBytes: NotRequired[Optional[int]]
    features: PlanFeatures
    public: NotRequired[bool]


class ProjectSettings(TypedDict):
    #: What new sessions and agent runs without a ``captcha`` option get (default "ask").
    captchaDefault: CaptchaMode
    #: What they get now: "ask" when the setting is "solve" but the plan no longer includes solving.
    captchaDefaultEffective: CaptchaMode
    #: The model used when a request names no provider or model (None: the server default).
    defaultModel: Optional["DefaultModel"]
    updatedAt: Optional[str]
    #: The project's own user who changed it last in the console (None for an API key or support).
    updatedBy: Optional[str]
    updatedVia: Optional[Literal["console", "api_key", "support"]]


class ModelKey(TypedDict):
    """One provider's own-key state. The key itself is never returned: ``preview`` is its last 4 characters."""

    provider: Provider
    #: The stored choice: calls use the project's own key or the platform's.
    use: KeySource
    #: What calls use now (the plan can override the choice: Free is always "project").
    useEffective: KeySource
    hasKey: bool
    preview: Optional[str]
    #: The provider accepted the key when it was saved (False: it could not be reached; the key was saved).
    verified: Optional[bool]
    createdAt: Optional[str]
    updatedAt: Optional[str]
    updatedVia: Optional[Literal["console", "api_key", "support"]]
    lastUsedAt: Optional[str]
    #: The platform can make calls for this provider for this project (a server key exists and the plan includes it).
    platformAvailable: bool


class ModelKeys(TypedDict):
    keys: List[ModelKey]


class Trajectories(TypedDict):
    enabled: bool
    noticeSeenAt: Optional[str]
    decidedBy: Optional[str]


class MeProject(Project):
    limits: Plan
    trajectories: Trajectories


class Me(TypedDict):
    #: None when calling with an API key.
    user: Optional[MeUser]
    #: Set when an admin is acting as this user from the admin panel.
    impersonatedBy: Optional[User]
    project: MeProject


class ApiKey(TypedDict):
    id: str
    name: str
    prefix: str
    createdAt: str
    lastUsedAt: Optional[str]


class NewApiKey(TypedDict):
    id: str
    name: str
    prefix: str
    #: The key itself: shown only in this response.
    key: str


# ---------------------------------------------------------------- sessions


class BrowserSettings(TypedDict):
    mode: Literal["standard", "realistic"]
    locale: Optional[str]
    timezone: Optional[str]


class CaptchaAttention(TypedDict):
    type: Literal["captcha"]
    kind: CaptchaKind
    url: str
    tabId: NotRequired[str]
    since: str
    #: "solving": being solved automatically; "waiting": a person's turn.
    state: NotRequired[Literal["solving", "waiting"]]
    reason: NotRequired[str]


class SessionUsage(TypedDict):
    seconds: float
    costUsd: float


class SessionData(TypedDict):
    id: str
    status: SessionStatus
    projectId: str
    region: str
    browser: bool
    shell: bool
    keepAlive: bool
    timeout: int
    #: Seconds without activity after which it ends (end reason "idle"); None: off.
    idleTimeout: NotRequired[Optional[int]]
    createdAt: str
    startedAt: Optional[str]
    endedAt: Optional[str]
    expiresAt: str
    #: released, timeout, idle (its idle timeout passed), disconnected, agent_finished, api_restart, paused_expired,
    #: machine_lost or account_recovered (see SessionEndReason).
    endReason: Optional[str]
    connectUrl: Optional[str]
    liveUrl: Optional[str]
    terminalUrl: Optional[str]
    #: The proxy or proxy rules as given, never with passwords; None without one.
    proxy: Union[None, Dict[str, Any], List[Dict[str, Any]]]
    browserSettings: BrowserSettings
    viewport: Optional[Viewport]
    trajectoriesEligible: bool
    captcha: CaptchaMode
    attention: Optional[CaptchaAttention]
    workspacePath: str
    profileId: Optional[str]
    profilePersist: NotRequired[bool]
    userMetadata: Dict[str, Any]
    moves: int
    checkpointAt: Optional[str]
    recoveries: int
    error: Optional[str]
    recordSession: bool
    hasRecording: bool
    #: When the recording, logs and agent-run steps were deleted under the plan's ``retentionDays`` (None until then).
    dataDeletedAt: NotRequired[Optional[str]]
    setup: List[str]
    setupStatus: Literal["none", "running", "done", "failed"]
    setupError: Optional[str]
    #: Requests to ad and tracker sites are refused inside the machine.
    blockAds: bool
    #: Requests refused so far (collected about every 30 s).
    blockedRequests: int
    #: "reject": consent banners are answered "Reject all" / "Necessary only", else hidden.
    cookieBanners: Literal["reject", "off"]
    #: The extension ids the session started with.
    extensions: List[str]
    #: The names of the session's env variables (values are never returned).
    env: NotRequired[List[str]]
    #: The names of the credentials its shell exports (a credential its profile links is listed too when it went into
    #: the shell).
    credentials: NotRequired[List[str]]
    usage: SessionUsage


class SessionUrls(TypedDict):
    liveUrl: Optional[str]
    terminalUrl: Optional[str]
    connectUrl: Optional[str]


class MoveTimings(TypedDict):
    captureMs: float
    acquireMs: float
    restoreMs: float
    totalMs: float


class StoppedProcess(TypedDict):
    pid: int
    #: The command line, credential values hidden, at most 200 characters.
    command: str
    #: How long it had run.
    seconds: int


class MoveShell(TypedDict):
    """Sessions with a shell, after a move: where the shell continues and which exported variables came along (the
    session's env and credentials are set again as well). Running processes do not move; the stopped ones are listed."""

    cwd: str
    exported: List[str]
    stoppedProcesses: List[StoppedProcess]


class BulkItem(TypedDict):
    id: str
    ok: bool
    error: NotRequired[str]


class BulkResult(TypedDict):
    results: List[BulkItem]


class Point(TypedDict):
    """Viewport coordinates in CSS pixels, from the top-left corner."""

    x: float
    y: float


class ActionResult(TypedDict):
    ok: bool
    action: str
    value: NotRequired[Any]
    #: One line saying what happened ("Dragged from (180, 200) to (400, 200) in 10 steps"); never typed text.
    text: NotRequired[str]
    error: NotRequired[str]
    #: A stable error code when there is one (e.g. "captcha_timeout"; for ``login``: "credential_login_failed",
    #: "credential_login_timeout", "credential_code_timeout", "credential_link_wrong_site").
    code: NotRequired[str]
    #: ``login``: the agent run that signed in (also in its value when it worked).
    runId: NotRequired[str]
    ms: int


class LoginValue(TypedDict):
    """What ``Session.login`` returns: the page the browser is on after signing in (no query or fragment) and the run
    that did it."""

    url: str
    title: str
    runId: str


class ModelUsage(TypedDict):
    inputTokens: int
    outputTokens: int
    costUsd: float


class StepElement(TypedDict):
    id: int
    role: str
    name: str


class StepResult(TypedDict):
    method: str
    description: str
    element: NotRequired[StepElement]
    #: The equivalent Playwright line.
    code: str
    data: NotRequired[Any]
    url: str
    title: str
    model: str
    usage: ModelUsage


class ExtractValue(TypedDict):
    data: Any
    model: str
    usage: ModelUsage


class GotoResult(TypedDict):
    url: str
    title: str
    status: Optional[int]


class PageContent(TypedDict):
    url: str
    title: str
    content: str


class PageElements(TypedDict):
    url: str
    title: str
    text: str
    #: One line per numbered interactive element.
    elements: str
    count: int


class Tab(TypedDict):
    index: int
    url: str
    title: str


class TabList(TypedDict):
    current: int
    tabs: List[Tab]


class ComputerResult(TypedDict):
    """What POST /v1/sessions/{id}/computer answers: what happened, and the screen after it."""

    ok: bool
    #: The provider's action name.
    action: str
    shape: Literal["anthropic", "openai"]
    text: str
    error: NotRequired[str]
    code: NotRequired[str]
    #: Base64 image, or None with screenshot=False.
    screenshot: Optional[str]
    mimeType: Optional[str]
    width: Optional[int]
    height: Optional[int]
    #: Screenshot pixels per CSS pixel.
    scale: float
    #: The pointer, in screenshot pixels.
    cursor: Point
    url: str
    title: str


class ExecResult(TypedDict):
    stdout: str
    stderr: str
    exitCode: Optional[int]
    timedOut: bool
    #: The output was cut (it was too long).
    truncated: bool
    durationMs: int


class ExecExit(TypedDict, total=False):
    type: Literal["exit"]
    exitCode: Optional[int]
    timedOut: bool
    durationMs: int
    truncated: bool


class FileEntry(TypedDict):
    name: str
    type: Literal["file", "dir", "other"]
    size: int
    mtime: str


class FileRef(TypedDict):
    path: str
    size: int


class CookieFile(TypedDict):
    path: str
    count: int


class SessionEvent(TypedDict):
    seq: int
    at: str
    type: Literal["console", "network", "navigation", "error", "lifecycle", "action", "exec", "captcha"]
    level: NotRequired[str]
    text: NotRequired[str]
    url: NotRequired[str]
    method: NotRequired[str]
    status: NotRequired[int]
    resourceType: NotRequired[str]
    durationMs: NotRequired[int]
    tabId: NotRequired[str]
    data: NotRequired[Dict[str, Any]]


class VisitedPage(TypedDict):
    tabId: str
    url: str
    title: str
    visits: int
    firstSeen: str
    lastSeen: str


class RecordingFrame(TypedDict):
    index: int
    at: str
    url: Optional[str]


class Recording(TypedDict):
    frames: List[RecordingFrame]
    durationMs: int


class Profile(TypedDict):
    id: str
    name: str
    sizeBytes: int
    createdAt: str
    updatedAt: str
    inUseBy: Optional[str]
    #: The name of the password credential it signs in with (``profiles.update(id, credential=...)``), or None.
    credential: Optional[str]


# ---------------------------------------------------------------- credentials

#: A credential holds a website password (with an optional 2FA key) or a secret (one value).
CredentialType = Literal["password", "secret"]

#: Where a credential may be used: "agent" (default): only the AI, as placeholders in agent runs, steps, scripts'
#: step() and the type action; "shell": only as environment variables in session shells and commands; "all": both.
CredentialScope = Literal["agent", "shell", "all"]

#: What ``Session.type_credential`` types from a password credential: its user name, its password or its current 2FA code
#: (with a ``codeSource`` of "push" or "url" it waits for a fresh one, up to ``codeTimeoutSeconds``).
CredentialField = Literal["username", "password", "otp"]

#: Where a password's 2FA codes come from: "totp" (an authenticator key, ``totp_secret``), "push" (your system sends each
#: code or sign-in link the site emails or texts: ``credentials.push_code``) or "url" (the platform asks your endpoint
#: ``code_url``, signed like a webhook). A password without one has no 2FA (``codeSource`` is None).
CredentialCodeSource = Literal["totp", "push", "url"]


class PasswordCredential(TypedDict):
    """A website password. Neither the password nor the 2FA key is ever returned."""

    #: Also its placeholder (``%NAME.password%``) and its shell variable (``$NAME_PASSWORD``).
    name: str
    type: Literal["password"]
    description: Optional[str]
    #: The sites where the AI may type it (1 to 20).
    origins: List[str]
    username: str
    #: It has a 2FA key (``codeSource`` is "totp"): ``%NAME.otp%`` and ``boxline-otp NAME`` give the current code.
    hasTotp: bool
    #: Where its 2FA codes come from; None: no 2FA.
    codeSource: Optional[CredentialCodeSource]
    #: The endpoint the platform asks for codes (``codeSource`` "url"); None otherwise. Its signing secret is never
    #: shown again.
    codeUrl: Optional[str]
    #: How long a wait for a pushed or asked code or link lasts (5 to 900 s, default 300).
    codeTimeoutSeconds: int
    #: The AI may use it in bash commands; this also allows exporting it into shells.
    shell: bool
    scope: CredentialScope
    createdAt: str
    updatedAt: str
    lastUsedAt: Optional[str]


class SecretCredential(TypedDict):
    """A secret (an API key, a token). Its value is never returned."""

    #: Also its placeholder (``%NAME%``) and its shell variable (``$NAME``).
    name: str
    type: Literal["secret"]
    description: Optional[str]
    #: Sites where the AI may type it (None = any site).
    origins: Optional[List[str]]
    #: "••••1a2b": the last 4 characters of values of 24 characters or more; None for shorter values and secrets with
    #: origins.
    preview: Optional[str]
    shell: bool
    scope: CredentialScope
    createdAt: str
    updatedAt: str
    lastUsedAt: Optional[str]


#: A credential without its values: ``c["type"]`` tells the two apart.
Credential = Union[PasswordCredential, SecretCredential]


class PasswordCredentialWritten(PasswordCredential, total=False):
    """A password as ``credentials.create`` and ``credentials.update`` return it: when ``code_url`` was set or changed it
    also has ``codeUrlSecret`` (``whsec_…``), the key that signs the platform's requests to ``code_url``, shown this
    once (a new one any time with ``credentials.rotate_code_url_secret``)."""

    codeUrlSecret: str


#: A credential as ``credentials.create`` and ``credentials.update`` return it.
CredentialWritten = Union[PasswordCredentialWritten, SecretCredential]


class CodeUrlSecret(TypedDict):
    """What ``credentials.rotate_code_url_secret`` returns."""

    codeUrlSecret: str


class CredentialCodeAccepted(TypedDict):
    """What ``credentials.push_code`` returns: the code or link is kept for a wait in progress (used once, at most 10
    minutes)."""

    accepted: Literal[True]
    kind: Literal["code", "link"]
    expiresAt: str


class CredentialUse(TypedDict):
    #: ``id`` is the session, or for ``agent_run`` the run and for ``task_run`` the task run.
    type: Literal["session", "exec", "agent_run", "step", "script", "task_run", "action", "otp"]
    id: str


class CredentialAuditEntry(TypedDict):
    """A change, or a use (once per session, command, agent run, step session, script, task run, typed field or
    ``boxline-otp`` session). Never values."""

    at: str
    #: "code": a pushed 2FA code or sign-in link (``details["kind"]``), never its value.
    action: Literal["create", "update", "delete", "use", "code"]
    type: CredentialType
    name: str
    #: Who changed it: "user:<email>", "key:<api key id>", or "support".
    actor: NotRequired[Optional[str]]
    #: What used it.
    usedBy: NotRequired[Optional[CredentialUse]]
    details: NotRequired[Dict[str, Any]]


# ---------------------------------------------------------------- web


class FetchResult(TypedDict):
    url: str
    finalUrl: str
    status: Optional[int]
    title: str
    content: str
    captcha: Optional[CaptchaKind]
    ms: int
    #: With links=True.
    links: NotRequired[List[str]]


class ExtractPage(TypedDict, total=False):
    """With several ``urls``, a page that did not load has ``status`` and ``finalUrl`` None and an ``error``."""

    url: str
    finalUrl: Optional[str]
    status: Optional[int]
    title: str
    error: "SearchError"


class ExtractResult(TypedDict):
    data: Any
    provider: Provider
    model: str
    keySource: KeySource
    usage: ModelUsage
    pages: List[ExtractPage]
    ms: int


class SearchPage(TypedDict):
    finalUrl: str
    status: Optional[int]
    title: str
    captcha: Optional[CaptchaKind]
    ms: int


class SearchError(TypedDict):
    code: str
    message: str


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    publishedAt: NotRequired[str]
    siteName: NotRequired[str]
    #: Fetched results only: the page as Markdown, or None when it could not be loaded.
    content: NotRequired[Optional[str]]
    page: NotRequired[Optional[SearchPage]]
    error: NotRequired[Optional[SearchError]]


class SearchResponse(TypedDict):
    #: The query as searched (white space collapsed).
    query: str
    results: List[SearchResult]
    ms: int
    #: Answered from this project's cache (the same search in the last hour): not counted.
    cached: bool


class CrawlPage(TypedDict):
    index: int
    url: str
    finalUrl: Optional[str]
    status: Optional[int]
    title: Optional[str]
    depth: int
    content: Optional[str]
    captcha: Optional[CaptchaKind]
    error: Optional[str]


class CrawlJob(TypedDict):
    id: str
    status: Literal["running", "completed", "failed", "canceled"]
    url: str
    params: Dict[str, Any]
    pagesDone: int
    pagesFailed: int
    skippedByRobots: int
    error: Optional[str]
    createdAt: str
    finishedAt: Optional[str]
    data: List[CrawlPage]
    #: The cursor of the next page of pages, or None.
    next: Optional[str]


# ---------------------------------------------------------------- agent


class ModelPrice(TypedDict):
    input: float
    output: float


class ModelInfo(TypedDict):
    id: str
    name: str
    note: str
    pricePerMTok: ModelPrice
    supportsEffort: bool
    #: Agent runs with mode="computer" work with this model.
    supportsComputerUse: NotRequired[bool]
    #: The computer-use tool the model gets in mode "computer" ("computer_20251124", "computer_20250124", "computer").
    computerTool: NotRequired[Optional[str]]
    default: bool


class ProviderInfo(TypedDict):
    id: Provider
    name: str
    #: A call could be made now for this project.
    available: bool
    #: Whose key a call would use now; None when there is none.
    keySource: Optional[KeySource]
    reason: Optional[str]
    models: List[ModelInfo]


class DefaultModel(TypedDict):
    provider: Provider
    model: str


class AgentModels(TypedDict):
    default: DefaultModel
    providers: List[ProviderInfo]


class AgentVariableSpec(TypedDict):
    """A variable with limits: ``origins`` are the sites whose fields may receive it
    (``"https://example.com"``, ``"https://*.example.com"``); ``shell`` lets bash commands use it."""

    value: str
    origins: NotRequired[List[str]]
    shell: NotRequired[bool]


#: A variable: the value, or the value with limits.
AgentVariable = Union[str, AgentVariableSpec]


AgentMode = Literal["tools", "computer"]

#: Structured output: a JSON Schema the answer must match, e.g. ``{"type": "object", "properties": {"title": {"type":
#: "string"}}, "required": ["title"]}``. At most 32 KB (as JSON), 2,000 parts and 32 levels deep, ``$ref`` only inside
#: the schema. Enforced: type, enum, const, properties, required, additionalProperties, items, prefixItems, min/maxItems,
#: uniqueItems, min/maxLength, minimum, maximum, exclusiveMinimum/Maximum, multipleOf, min/maxProperties, allOf, anyOf,
#: oneOf, not, $ref; given to the model only: pattern, patternProperties, format. An answer that does not match gets one
#: repair try, then the run fails with errorCode "output_invalid".
OutputSchema = Dict[str, Any]


#: Why a session ended.
SessionEndReason = Literal[
    "released", "timeout", "idle", "disconnected", "agent_finished", "api_restart", "paused_expired", "machine_lost", "account_recovered"
]

#: A run's ``errorCode``. The first four are limits: the run stopped without finishing and can be continued
#: (``agent.continue_run``) while ``continuable`` is set, and so can "server_restarted" (the API server running it stopped
#: while its session went on). "session_timeout": its session reached its time limit;
#: "session_ended": its session ended another way (``error`` names how); "output_invalid": the answer did not match the
#: output schema; "internal": an error on the platform's side. Other codes are those of the platform error that stopped it.
AgentRunErrorCode = Literal[
    "spend_limit", "max_steps", "max_cost", "too_many_errors", "no_progress", "server_restarted", "session_timeout", "session_ended",
    "output_invalid", "internal", "out_of_credit"
]


class AgentRunStarted(TypedDict):
    id: str
    status: Literal["running"]
    sessionId: str
    #: When the run's session ends by its time limit (the run stops then, errorCode "session_timeout").
    sessionExpiresAt: NotRequired[Optional[str]]
    provider: Provider
    model: str
    #: Whose key pays for the run's model calls: "project" is the project's own key (no model charge from Boxline).
    keySource: KeySource
    mode: NotRequired[AgentMode]


class Continuable(TypedDict):
    #: Until when ``agent.continue_run`` can carry the run on (its own session is kept that long).
    until: str


class AgentMessageSent(TypedDict):
    """``agent.send_message``'s answer: queued for the agent's next step."""

    id: str
    at: str
    delivered: Literal[False]


class AgentStep(TypedDict):
    #: message: a message you sent (``agent.send_message``), recorded when the model received it.
    #: code: the run waits for a password's 2FA code or sign-in link (``credentials.push_code``, or your ``code_url``);
    #: see ``state``.
    type: Literal["text", "tool", "handover", "handback", "captcha", "message", "code"]
    at: str
    #: message steps: its id and when it was sent (``at`` is when the model got it); they also carry ``"from": "user"``
    #: (a key a TypedDict cannot name).
    id: NotRequired[str]
    sentAt: NotRequired[str]
    delivered: NotRequired[bool]
    #: handover: who paused the run; captcha "solved": who solved it.
    by: NotRequired[Literal["user", "agent", "captcha", "auto", "person"]]
    text: NotRequired[str]
    #: tool and handover steps: the model's own words with the call (what it is doing and will do next), redacted.
    thought: NotRequired[str]
    name: NotRequired[str]
    #: A tool's input as the model wrote it: variables appear as %name%, never as their values.
    input: NotRequired[Any]
    output: NotRequired[str]
    isError: NotRequired[bool]
    ms: NotRequired[int]
    #: captcha steps: "solving", "waiting" (a person's turn) or "solved"; code steps: "waiting" (a wait began: push the
    #: code or link now), then "received" or "timeout". Never the code or the link.
    state: NotRequired[Literal["solving", "waiting", "solved", "received", "timeout"]]
    #: code steps: the password credential whose code or link the run waits for.
    credential: NotRequired[str]
    #: captcha steps: the CAPTCHA kind; code steps: "code" or "link".
    kind: NotRequired[str]
    host: NotRequired[str]
    reason: NotRequired[str]


class AgentUsage(TypedDict):
    inputTokens: int
    outputTokens: int
    costUsd: Optional[float]


class Handover(TypedDict):
    by: Literal["user", "agent", "captcha"]
    reason: Optional[str]


class AgentRun(TypedDict):
    id: str
    status: Literal["running", "paused", "completed", "failed", "canceled"]
    task: str
    sessionId: str
    #: The session's expiresAt: the run stops then (session_timeout).
    sessionExpiresAt: NotRequired[Optional[str]]
    provider: Provider
    model: str
    #: Whose key pays for the run's model calls: "project" is the project's own key (no model charge from Boxline).
    keySource: KeySource
    mode: NotRequired[AgentMode]
    #: Its limits: steps (None: none), model cost in USD (None: none), tool errors in a row.
    maxSteps: NotRequired[Optional[int]]
    maxCostUsd: NotRequired[Optional[float]]
    maxConsecutiveErrors: NotRequired[int]
    steps: List[AgentStep]
    #: The final text of a completed run; with an ``output`` schema, the JSON answer (a dict, list, …). None unless it
    #: completed.
    result: Any
    #: The final text; with an ``output`` schema, the model's one-sentence summary of the answer; for a run that stopped at
    #: a limit, the model's short account of what is done and what is left.
    resultText: NotRequired[Optional[str]]
    #: The run's output schema, or None.
    output: NotRequired[Optional[OutputSchema]]
    error: Optional[str]
    #: See AgentRunErrorCode ("max_steps", "output_invalid", …).
    errorCode: NotRequired[Optional[str]]
    #: Set while the run can be continued (``agent.continue_run``); None otherwise.
    continuable: NotRequired[Optional[Continuable]]
    #: The run this one continues, and the run that continued this one.
    continuedFrom: NotRequired[Optional[str]]
    continuedBy: NotRequired[Optional[str]]
    #: The names of the run's own variables (never values): continue_run needs their values again.
    variableNames: NotRequired[List[str]]
    usage: AgentUsage
    handover: Optional[Handover]
    #: Set when a task started the run (tasks.run, or its schedule).
    taskId: NotRequired[Optional[str]]
    taskRunId: NotRequired[Optional[str]]
    createdAt: str
    finishedAt: Optional[str]


#: One event of an agent run's live stream (Agent.stream): a step, or ``{"type": "thought" | "status" | "exec" | "output" |
#: "done", …}``; ``thought`` (``text``, ``at``) comes as soon as the model's reply arrives, before its tool runs. ``done``
#: carries ``status``, ``result`` (JSON with an output schema), ``resultText``, ``error``, ``errorCode`` and ``continuable``.
AgentRunEvent = Dict[str, Any]


# ---------------------------------------------------------------- tasks


class TaskVariable(TypedDict):
    """A variable of a task, written in its instruction as ``%name%``. Plain (the default): the value (from the run,
    else the schedule, else ``default``) is written into the instruction, so the model reads it, and is kept with the
    run. ``secret: True``: the value is never stored (so no ``default``), must come with every run, and is typed by the
    agent without the model seeing it (``origins``, ``shell`` as for agent-run variables); such a task cannot have a
    schedule."""

    #: Letters, digits and _ (not starting with a digit), 64 characters at most.
    name: str
    secret: NotRequired[bool]
    #: Plain variables only.
    default: NotRequired[Optional[str]]
    description: NotRequired[Optional[str]]
    #: Secret variables only: the sites whose fields may receive the value.
    origins: NotRequired[Optional[List[str]]]
    #: Secret variables only: bash commands may use it.
    shell: NotRequired[bool]


class TaskSchedule(TypedDict):
    #: Five fields (minute hour day-of-month month day-of-week), or @hourly, @daily, @weekly, @monthly, @yearly.
    cron: str
    #: An IANA time zone the cron is read in.
    timezone: str
    #: Values for the task's plain variables.
    variables: Dict[str, str]
    enabled: bool
    #: When it runs next; None while switched off.
    nextRunAt: Optional[str]
    #: The next 3 times it fires, ISO in UTC (the first is nextRunAt), on the schedule's time zone and DST; [] while
    #: switched off.
    nextRuns: List[str]


class TaskLastRun(TypedDict):
    """A task's newest run (by hand or scheduled, queued included; skipped and missed times are not runs)."""

    id: str
    #: As in the run history: it follows the agent run while that works.
    status: Literal["queued", "running", "paused", "completed", "failed", "canceled"]
    #: Why it stopped ("max_steps", "session_timeout", …), as on its agent run; None while working or when completed.
    errorCode: Optional[str]
    createdAt: str
    finishedAt: Optional[str]


class TaskProfile(TypedDict):
    id: str
    persist: bool


class Task(TypedDict):
    """A task as stored: secret variables without values, ``browser.proxy`` without its password."""

    id: str
    name: str
    instruction: str
    variables: List[TaskVariable]
    #: Names of the credentials its runs get as placeholders (never values).
    credentials: List[str]
    output: Optional[OutputSchema]
    #: The settings of each run's own session (``shell``, ``proxy``, ``captcha``, ``mode``, ``locale``, ``timezone``,
    #: ``viewport``, ``timeout``, ``idleTimeout``, ``blockAds``, ``cookieBanners``, ``extensions``, ``allowWithExtensions``).
    browser: Optional[Dict[str, Any]]
    profile: Optional[TaskProfile]
    #: ``{"provider", "model"}``, or None for the server's default.
    model: Optional[Dict[str, str]]
    #: None: no step limit (a task saved without one shows 30).
    maxSteps: Optional[int]
    #: Each run's money budget (USD of model cost), or None.
    maxCostUsd: NotRequired[Optional[float]]
    notifyOnFailure: Optional[str]
    schedule: Optional[TaskSchedule]
    lastRunAt: Optional[str]
    #: The newest run, or None before the first.
    lastRun: Optional[TaskLastRun]
    createdAt: str
    updatedAt: str


#: queued: a scheduled run waiting for its turn to start; running, paused (a person has the browser), then completed,
#: failed or canceled, as its agent run; skipped and missed: a scheduled time that did not run.
TaskRunStatus = Literal["queued", "running", "paused", "completed", "failed", "canceled", "skipped", "missed"]


class TaskRun(TypedDict):
    """One run of a task (by hand or on its schedule), or a scheduled time that did not run."""

    id: str
    taskId: str
    #: The agent run (agent.get(runId) has its steps); None while queued, for skipped and missed times, and for a
    #: scheduled run that could not start.
    runId: Optional[str]
    sessionId: Optional[str]
    status: TaskRunStatus
    scheduled: bool
    scheduledFor: Optional[str]
    #: skipped and missed: "previous_run_running", "plan_limit" or "not_running".
    reason: Optional[str]
    #: missed: how many scheduled times it stands for (counted up to 1,000).
    missedCount: Optional[int]
    #: The plain values it ran with.
    variables: Dict[str, str]
    #: The names of its secret variables, never their values.
    secretVariables: List[str]
    #: The result is JSON (the task has an output schema).
    structured: bool
    #: The JSON answer (structured) or the final text; None until it completes.
    result: Any
    resultText: Optional[str]
    error: Optional[str]
    #: e.g. "output_invalid", or why a scheduled run could not start ("no_capacity", "missing_variables", "not_started", …).
    errorCode: Optional[str]
    usage: AgentUsage
    durationMs: Optional[int]
    createdAt: str
    finishedAt: Optional[str]


# ---------------------------------------------------------------- usage and prices


class ProxyUsage(TypedDict):
    residentialGb: float
    datacenterGb: float
    customGb: float
    costUsd: float


class CaptchaSolves(TypedDict):
    solved: int
    failed: int
    refused: int
    costUsd: float


class SearchUsage(TypedDict):
    count: int
    costUsd: float


class UsageDay(TypedDict):
    date: str
    seconds: float
    costUsd: float


Usage = TypedDict(
    "Usage",
    {
        "from": str,
        "to": str,
        "sessions": int,
        "running": int,
        "browserSeconds": float,
        "sandboxVcpuSeconds": float,
        "sandboxGibSeconds": float,
        "proxy": ProxyUsage,
        "captchaSolves": CaptchaSolves,
        "searches": SearchUsage,
        "costUsd": float,
        "byDay": List[UsageDay],
    },
)


class StatsDay(TypedDict):
    sessions: int
    browserSeconds: float
    costUsd: float
    agentRuns: int
    #: Model cost on the platform's keys; runs on the project's own keys are in ownKeyModelCostUsd.
    modelCostUsd: float
    ownKeyModelCostUsd: float


class StatsByDay(StatsDay):
    date: str


class Stats(TypedDict):
    days: int
    running: int
    concurrencyLimit: int
    totals: StatsDay
    byDay: List[StatsByDay]


class Pricing(TypedDict, total=False):
    currency: str
    billing: str
    browserSessionPerHour: float
    sandboxPerVcpuHour: float
    sandboxPerGibHour: float
    defaultShellMachine: Dict[str, float]
    pausedSessions: Dict[str, float]
    proxies: Dict[str, float]
    captchaSolving: Dict[str, float]
    #: {"byPlan": {plan: {"includedPerMonth", "extraPer1000Usd"}}}
    webSearch: Dict[str, Dict[str, Dict[str, Optional[float]]]]
    plans: List[Plan]
    features: Dict[str, str]


# ---------------------------------------------------------------- webhooks

#: The events an endpoint can subscribe to (``webhook.test`` needs no subscription). New types may be added: an
#: endpoint subscribed to "*" gets them too, so a receiver should ignore types it does not know.
WebhookEventType = Literal[
    "session.started",
    "session.expiring",
    "session.ended",
    "agent_run.started",
    "agent_run.waiting",
    "agent_run.resumed",
    "agent_run.finished",
    "captcha.waiting",
    "captcha.solved",
    "captcha.failed",
    "crawl.finished",
    "task_run.started",
    "task_run.finished",
    "task.schedule_paused",
    "usage.limit_reached",
    "api_key.created",
    "api_key.revoked",
    "credential.code_needed",
    "credential.changed",
    "webhook.changed",
    "webhook.disabled",
    "extension.uploaded",
    "extension.deleted",
]

#: What an endpoint subscribes to: event types, or "*" for all of them (those added later too).
WebhookSubscription = Union[WebhookEventType, Literal["*"]]


class WebhookEventTypeInfo(TypedDict):
    type: WebhookEventType
    #: For pickers ("sessions", "agent runs", …).
    group: str
    description: str


class WebhookEventTypeList(TypedDict):
    data: List[WebhookEventTypeInfo]
    #: "*": subscribe to it for every type.
    all: Literal["*"]

#: Why a delivery or an attempt failed (None when delivered); the last five are deliveries that were never sent.
WebhookErrorCode = Literal[
    "bad_status",
    "gone",
    "timeout",
    "connection_failed",
    "tls_failed",
    "address_not_allowed",
    "gateway_unavailable",
    "endpoint_disabled",
    "project_suspended",
    "payload_expired",
    "queue_full",
    "signing_failed",
]


class WebhookEndpoint(TypedDict):
    id: str
    url: str
    #: The types it gets, or ["*"] for all of them.
    events: List[WebhookSubscription]
    description: Optional[str]
    enabled: bool
    #: "user": switched off with update; "gone": it answered 410; "failing": 3 days of failed deliveries.
    #: "account_recovered": a password reset recovered an account whose email had not been confirmed.
    disabledReason: Optional[Literal["user", "gone", "failing", "account_recovered"]]
    disabledAt: Optional[str]
    failingSince: Optional[str]
    lastSuccessAt: Optional[str]
    lastFailureAt: Optional[str]
    secretRotatedAt: Optional[str]
    #: Until then deliveries are also signed with the secret before the last rotation.
    previousSecretExpiresAt: Optional[str]
    #: Deliveries wait until then because the endpoint timed out 3 times in a row (test events still go).
    pausedUntil: Optional[str]
    createdAt: str
    updatedAt: str


class NewWebhookEndpoint(WebhookEndpoint):
    #: The signing secret, "whsec_…": shown only in this response.
    secret: str


class WebhookAttempt(TypedDict):
    at: str
    status: Optional[int]
    durationMs: int
    error: Optional[str]
    errorCode: Optional[WebhookErrorCode]


class WebhookEvent(TypedDict):
    """The body of every delivery. Verify it with verify_webhook() first, and drop an ``id`` already handled. Each
    type's ``data`` is below (``WebhookEventPayload`` is the union keyed by ``type``)."""

    id: str
    type: str
    createdAt: str
    projectId: str
    #: Set on "send test" deliveries (webhook.test, and samples of other types): made-up data.
    test: NotRequired[bool]
    data: Dict[str, Any]


#: Who made a change: "user:<email>" (a console login), "key:<api key id>", "support" (support acting as you) or
#: "platform".
WebhookActor = str


class WebhookSessionExpiringData(TypedDict):
    sessionId: str
    expiresAt: str
    secondsLeft: int
    userMetadata: Optional[Dict[str, Any]]
    userMetadataTruncated: NotRequired[bool]


class WebhookAgentRunStartedData(TypedDict):
    id: str
    status: Literal["running"]
    #: At most 4000 characters; variables are their %name%.
    task: str
    sessionId: str
    provider: Provider
    model: str
    mode: Literal["tools", "computer"]
    continuedFrom: Optional[str]
    taskId: Optional[str]
    taskRunId: Optional[str]
    createdAt: str
    truncated: NotRequired[bool]


class WebhookAgentRunWaitingData(TypedDict):
    id: str
    sessionId: Optional[str]
    state: Literal["waiting"]
    #: "agent": it asked for help; "user": someone took over; "captcha": a CAPTCHA needs a person.
    by: Literal["agent", "user", "captcha"]
    #: At most 500 characters, variables as their %name%.
    reason: Optional[str]
    #: The run in the console (a login is needed). The signed live view is never sent: get it with sessions.live().
    consoleUrl: str
    since: str


class WebhookAgentRunResumedData(TypedDict):
    id: str
    sessionId: Optional[str]
    state: Literal["resumed"]
    #: What it waited for, as in agent_run.waiting.
    by: Literal["agent", "user", "captcha"]
    #: "handback", "message" (a message answered its request for help) or "solved" (the CAPTCHA).
    via: Literal["handback", "message", "solved"]
    at: str


class WebhookCaptchaOutcomeData(TypedDict):
    sessionId: str
    #: The agent run working in the session, if any.
    runId: Optional[str]
    kind: CaptchaKind
    host: str
    #: "auto": automatic solving; "person": it cleared while it waited for a person.
    by: Literal["auto", "person"]
    ms: int
    #: captcha.failed: why automatic solving failed.
    reason: NotRequired[str]


class WebhookTaskRunStartedData(TypedDict):
    taskId: str
    taskName: Optional[str]
    taskRunId: str
    runId: str
    sessionId: Optional[str]
    status: Literal["running"]
    scheduled: bool
    scheduledFor: Optional[str]
    #: Names only, never values.
    variables: List[str]
    startedAt: str


class WebhookSchedulePausedData(TypedDict):
    taskId: str
    taskName: Optional[str]
    #: "schedule_invalid": it could not be read; "schedule_ended": it has no more times.
    reason: Literal["schedule_invalid", "schedule_ended"]
    message: str
    pausedAt: str


class WebhookUsageLimitData(TypedDict):
    #: The plan's limits, or the organization's money: its monthly spending limit (``spend_limit``), the project's own
    #: (``project_spend_limit``), or its credit used up (``credit``).
    kind: Literal["model_spend", "proxy_gb", "captcha_solves", "searches", "concurrency", "spend_limit", "project_spend_limit", "credit"]
    #: USD (model spend, spending limits; for ``credit`` the credit used this month), GB, a count, or sessions at once.
    limit: float
    used: float
    #: "2026-09" (a UTC month), or "2026-09-30T14" (a UTC hour) for concurrency.
    period: str
    #: When a monthly limit starts over; None for concurrency.
    resetsAt: Optional[str]


class WebhookApiKeyData(TypedDict):
    id: str
    #: The key's first 12 characters, as api_keys.list shows.
    prefix: str
    name: str
    by: WebhookActor


class WebhookCredentialCodeNeededData(TypedDict):
    #: The password credential whose code or sign-in link is awaited.
    credential: str
    #: What the site sends: a 2FA code or a sign-in link. Forward it now with ``credentials.push_code``.
    type: Literal["code", "link"]
    sessionId: str
    #: The agent run that waits; None for an action or ``boxline-otp``.
    runId: Optional[str]


class WebhookCredentialChangedData(TypedDict):
    #: The credential's name.
    name: str
    action: Literal["created", "updated", "deleted"]
    type: CredentialType
    by: WebhookActor
    #: Field names an update changed ("password", "origins", …; "profiles" when it was linked to or unlinked from a
    #: profile).
    changed: NotRequired[List[str]]


class WebhookChangedData(TypedDict):
    endpointId: str
    host: str
    action: Literal["created", "updated", "deleted", "secret_rotated"]
    events: List[WebhookSubscription]
    enabled: bool
    by: WebhookActor
    changed: NotRequired[List[Literal["url", "events", "enabled", "description"]]]


class WebhookExtensionData(TypedDict):
    id: str
    name: str
    version: Optional[str]
    sha256: Optional[str]
    by: WebhookActor


class _TypedEvent(TypedDict):
    id: str
    createdAt: str
    projectId: str
    test: NotRequired[bool]


class SessionStartedEvent(_TypedEvent):
    type: Literal["session.started"]
    #: The session as sessions.get returns it, with every URL (and attention) None.
    data: Dict[str, Any]


class SessionExpiringEvent(_TypedEvent):
    type: Literal["session.expiring"]
    data: WebhookSessionExpiringData


class SessionEndedEvent(_TypedEvent):
    type: Literal["session.ended"]
    data: Dict[str, Any]


class AgentRunStartedEvent(_TypedEvent):
    type: Literal["agent_run.started"]
    data: WebhookAgentRunStartedData


class AgentRunWaitingEvent(_TypedEvent):
    type: Literal["agent_run.waiting"]
    data: WebhookAgentRunWaitingData


class AgentRunResumedEvent(_TypedEvent):
    type: Literal["agent_run.resumed"]
    data: WebhookAgentRunResumedData


class AgentRunFinishedEvent(_TypedEvent):
    type: Literal["agent_run.finished"]
    data: Dict[str, Any]


class CaptchaWaitingEvent(_TypedEvent):
    type: Literal["captcha.waiting"]
    data: Dict[str, Any]


class CaptchaSolvedEvent(_TypedEvent):
    type: Literal["captcha.solved"]
    data: WebhookCaptchaOutcomeData


class CaptchaFailedEvent(_TypedEvent):
    type: Literal["captcha.failed"]
    data: WebhookCaptchaOutcomeData


class CrawlFinishedEvent(_TypedEvent):
    type: Literal["crawl.finished"]
    data: Dict[str, Any]


class TaskRunStartedEvent(_TypedEvent):
    type: Literal["task_run.started"]
    data: WebhookTaskRunStartedData


class TaskRunFinishedEvent(_TypedEvent):
    type: Literal["task_run.finished"]
    data: Dict[str, Any]


class TaskSchedulePausedEvent(_TypedEvent):
    type: Literal["task.schedule_paused"]
    data: WebhookSchedulePausedData


class UsageLimitReachedEvent(_TypedEvent):
    type: Literal["usage.limit_reached"]
    data: WebhookUsageLimitData


class ApiKeyCreatedEvent(_TypedEvent):
    type: Literal["api_key.created"]
    data: WebhookApiKeyData


class ApiKeyRevokedEvent(_TypedEvent):
    type: Literal["api_key.revoked"]
    data: WebhookApiKeyData


class CredentialCodeNeededEvent(_TypedEvent):
    type: Literal["credential.code_needed"]
    data: WebhookCredentialCodeNeededData


class CredentialChangedEvent(_TypedEvent):
    type: Literal["credential.changed"]
    data: WebhookCredentialChangedData


class WebhookChangedEvent(_TypedEvent):
    type: Literal["webhook.changed"]
    data: WebhookChangedData


class WebhookDisabledEvent(_TypedEvent):
    type: Literal["webhook.disabled"]
    data: Dict[str, Any]


class ExtensionUploadedEvent(_TypedEvent):
    type: Literal["extension.uploaded"]
    data: WebhookExtensionData


class ExtensionDeletedEvent(_TypedEvent):
    type: Literal["extension.deleted"]
    data: WebhookExtensionData


class WebhookTestEvent(_TypedEvent):
    type: Literal["webhook.test"]
    data: Dict[str, Any]


#: A verified delivery as a union keyed by ``type`` (check ``event["type"]``, then read ``event["data"]``).
WebhookEventPayload = Union[
    SessionStartedEvent,
    SessionExpiringEvent,
    SessionEndedEvent,
    AgentRunStartedEvent,
    AgentRunWaitingEvent,
    AgentRunResumedEvent,
    AgentRunFinishedEvent,
    CaptchaWaitingEvent,
    CaptchaSolvedEvent,
    CaptchaFailedEvent,
    CrawlFinishedEvent,
    TaskRunStartedEvent,
    TaskRunFinishedEvent,
    TaskSchedulePausedEvent,
    UsageLimitReachedEvent,
    ApiKeyCreatedEvent,
    ApiKeyRevokedEvent,
    CredentialCodeNeededEvent,
    CredentialChangedEvent,
    WebhookChangedEvent,
    WebhookDisabledEvent,
    ExtensionUploadedEvent,
    ExtensionDeletedEvent,
    WebhookTestEvent,
]


class WebhookDelivery(TypedDict):
    id: str
    endpointId: str
    #: The event's id: the same on every endpoint and every attempt (dedupe by it).
    eventId: str
    eventType: str
    test: bool
    status: Literal["pending", "delivered", "failed"]
    attempts: int
    nextAttemptAt: Optional[str]
    lastAttemptAt: Optional[str]
    deliveredAt: Optional[str]
    responseStatus: Optional[int]
    responseBody: Optional[str]
    durationMs: Optional[int]
    error: Optional[str]
    errorCode: Optional[WebhookErrorCode]
    #: The last 20 attempts.
    history: List[WebhookAttempt]
    #: The body that was sent (the event); None after 7 days.
    payload: Optional[WebhookEvent]
    createdAt: str


# ---------------------------------------------------------------- extensions


class Extension(TypedDict):
    """An uploaded Chrome extension (Manifest V3)."""

    id: str
    #: From the manifest (resolved from _locales when it uses __MSG_…__).
    name: str
    version: str
    description: str
    permissions: List[str]
    hostPermissions: List[str]
    #: Size of the zip.
    sizeBytes: int
    files: int
    #: Of the zip.
    sha256: str
    createdAt: str
