"""The Boxline client, async and sync.

This file is the source of both clients: ``AsyncBoxline`` lives here, and ``_client.py`` (``Boxline``) is generated
from it by ``scripts/unasync.py`` (``await``/``async`` removed, ``Async*`` names renamed). Edit this file, then run
``python scripts/unasync.py``; tests/test_unasync.py fails when the two differ.
"""

from __future__ import annotations

# --- async only ---
import asyncio
# --- end ---
import base64
import os
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

import httpx

from . import types as t
from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    JSON,
    NOT_GIVEN,
    STEP_TIMEOUT,
    Config,
    Proxy,
    RequestOptions,
    SSEParser,
    as_list,
    camel,
    clean,
    json_line,
    plan_request,
    query,
    retry_delay,
    seg,
    timeout_for,
)
from ._errors import BoxlineConnectionError, BoxlineError, BoxlineTimeoutError, CaptchaTimeoutError, ErrorCode, NotFoundError, error_from_response, make_error
from ._pagination import AsyncPage, AsyncPager

# exec waits for the session's setup commands first (the API waits up to 10 minutes), then runs the command.
SETUP_WAIT_S = 600

T = TypeVar("T")

#: HTTP status an action result's code stands for, when one action of a list fails (the call itself answered 200).
_ACTION_STATUS = {"captcha_timeout": 409, "model_refused": 422}

#: One action: a dict (``{"action": "goto", "url": …}``) or a bare string, which is a plain-English step.
ActionItem = Union[JSON, str]

#: A point: ``(x, y)``, ``{"x": …, "y": …}``, or (for drag ends) a CSS selector.
PointLike = Union[Tuple[float, float], Dict[str, float], str]


def _point(p: PointLike) -> Union[Dict[str, float], str]:
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        return {"x": p["x"], "y": p["y"]}
    x, y = p
    return {"x": x, "y": y}


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _parse(res: httpx.Response) -> Any:
    if res.status_code == 204 or not res.content:
        return None
    try:
        return res.json()
    except ValueError:
        raise BoxlineError(res.status_code, "invalid_response", f"{res.request.method} {res.request.url.path} answered something that is not JSON", request_id=res.headers.get("x-request-id")) from None


async def _sse(lines: AsyncIterator[str]) -> AsyncIterator[JSON]:
    parser = SSEParser()
    async for line in lines:
        event = parser.feed(line)
        if event is not None:
            yield event
    last = parser.flush()
    if last is not None:
        yield last


class AsyncBoxline:
    """Client for the Boxline API: isolated cloud sessions with a Chrome browser, a bash shell and a shared disk,
    plus the web APIs and the AI agent.

    ``api_key`` defaults to ``BOXLINE_API_KEY`` and ``base_url`` to ``BOXLINE_API_URL``, then https://api.boxline.dev
    (for a local API, http://localhost:8080).
    ``timeout`` is the time limit of one request in seconds; ``max_retries`` how often a GET, or a POST with an
    Idempotency-Key, is tried again after a network error, 429 or 5xx (with backoff). Use it as a context manager
    (``async with``), or call ``aclose``, to close its connections::

        async with AsyncBoxline() as bx:
            s = await bx.sessions.create()
            async for session in bx.sessions.list(status="RUNNING"):
                print(session.id)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.AsyncClient] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._cfg = Config.make(api_key, base_url, timeout, max_retries, headers)
        self._own_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self._cfg.timeout)
        self.api_key = self._cfg.api_key
        self.base_url = self._cfg.base_url
        self.timeout = self._cfg.timeout
        self.max_retries = self._cfg.max_retries
        self.auth = AsyncAuth(self)
        self.project = AsyncProject(self)
        self.api_keys = AsyncApiKeys(self)
        self.sessions = AsyncSessions(self)
        self.contexts = AsyncContexts(self)
        self.crawl = AsyncCrawl(self)
        self.agent = AsyncAgent(self)
        self.tasks = AsyncTasks(self)
        self.secrets = AsyncSecrets(self)
        self.webhooks = AsyncWebhooks(self)
        self.extensions = AsyncExtensions(self)

    def with_options(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> "AsyncBoxline":
        """A client like this one with some options changed, e.g. ``bx.with_options(max_retries=5)``. It shares
        this client's connections."""
        return AsyncBoxline(
            api_key=api_key or self.api_key,
            base_url=base_url or self.base_url,
            timeout=self.timeout if timeout is None else timeout,
            http_client=self._http,
            max_retries=self.max_retries if max_retries is None else max_retries,
            headers={**self._cfg.headers, **(headers or {})},
        )

    async def aclose(self) -> None:
        """Closes the client's connections (not those of an ``http_client`` you passed in)."""
        if self._own_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncBoxline":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ----- plumbing

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        content: Optional[Union[bytes, str]] = None,
        params: Optional[Mapping[str, str]] = None,
        options: Optional[RequestOptions] = None,
        timeout: Optional[float] = None,
        stream: bool = False,
    ) -> httpx.Response:
        """One API call with the SDK's reliability rules: retries (``max_retries``) with exponential backoff and
        jitter after network errors, timeouts, 429 and 5xx, honouring Retry-After and RateLimit-Reset, only for GETs
        and for the POSTs that carry an Idempotency-Key (the SDK makes one per call for those, so a retry never
        creates a second session, run or crawl); a time limit per attempt; typed errors with the request id."""
        plan = plan_request(self._cfg, method, path, options, timeout, json is not None)
        if content is not None:
            plan.headers.setdefault("content-type", "application/octet-stream")
        attempt = 0
        while True:
            try:
                req = self._http.build_request(method, self.base_url + path, json=json, content=content, params=params, headers=plan.headers, timeout=timeout_for(plan.timeout, stream))
                res = await self._http.send(req, stream=stream)
            except httpx.TimeoutException:
                error: BoxlineError = BoxlineTimeoutError(f"{method} {path.split('?')[0]} timed out after {plan.timeout:g} s", plan.headers.get("x-client-request-id"))
            except httpx.TransportError as exc:
                error = BoxlineConnectionError(f"{method} {path.split('?')[0]} could not reach {self.base_url}: {exc or type(exc).__name__}", client_request_id=plan.headers.get("x-client-request-id"))
            else:
                if res.status_code < 400:
                    return res
                await res.aread()
                await res.aclose()
                error = error_from_response(res)
            if attempt >= plan.max_retries or not error.retryable:
                raise error
            delay = retry_delay(attempt, error)
            if delay is None:
                raise error
            await _sleep(delay)
            attempt += 1

    async def _json(self, method: str, path: str, *, json: Any = None, params: Optional[Mapping[str, Any]] = None, options: Optional[RequestOptions] = None, timeout: Optional[float] = None) -> Any:
        res = await self._send(method, path, json=json, params=query(params or {}), options=options, timeout=timeout)
        return _parse(res)

    async def _bytes(self, method: str, path: str, *, json: Any = None, params: Optional[Mapping[str, Any]] = None, options: Optional[RequestOptions] = None) -> bytes:
        res = await self._send(method, path, json=json, params=query(params or {}), options=options)
        return res.content

    async def _stream(self, method: str, path: str, *, json: Any = None, params: Optional[Mapping[str, Any]] = None, options: Optional[RequestOptions] = None) -> httpx.Response:
        return await self._send(method, path, json=json, params=query(params or {}), options=options, stream=True)

    def _list(self, path: str, params: Mapping[str, Any], wrap: Callable[[Any], T], options: Optional[RequestOptions]) -> "AsyncPager[T]":
        async def load(after: Optional[str]) -> AsyncPage[T]:
            q = dict(params)
            if after is not None:
                q["after"] = after
            raw = await self._json("GET", path, params=q, options=options)
            return AsyncPage(raw, [wrap(x) for x in raw.get("data") or []], load)

        return AsyncPager(load)

    async def request(self, method: str, path: str, body: Any = None, *, params: Optional[Mapping[str, Any]] = None, options: Optional[RequestOptions] = None) -> Any:
        """Low-level JSON request; returns the parsed body (None for empty replies). The retry and error rules apply."""
        return await self._json(method, path, json=body, params=params, options=options)

    # ----- account

    async def me(self, *, options: Optional[RequestOptions] = None) -> t.Me:
        """The calling user and project, its plan's limits and features, and whether it is suspended."""
        return await self._json("GET", "/v1/auth/me", options=options)

    async def has_feature(self, feature: str, *, options: Optional[RequestOptions] = None) -> bool:
        """True when the project's plan includes ``feature`` (calls that need a missing one fail with 402 feature_not_in_plan)."""
        me = await self.me(options=options)
        return bool(me["project"]["limits"]["features"].get(feature))  # type: ignore[misc]

    # ----- web

    async def fetch(
        self,
        url: str,
        format: str = "markdown",
        timeout_ms: Optional[int] = None,
        wait_until: Optional[str] = None,
        proxy: Optional[Proxy] = None,
        browser: Optional[Union[bool, JSON]] = None,
        delay_ms: Optional[int] = None,
        viewport: Optional[Dict[str, int]] = None,
        links: Optional[bool] = None,
        block_ads: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.FetchResult:
        """Loads a page in a real browser (inside a sandbox) and returns its content (markdown, html or text).
        ``links=True`` also returns the page's absolute links. ``proxy``: see ``sessions.create``; the IP rotates per
        request unless ``"ip": "sticky"``."""
        body = clean({"url": url, "format": format, "timeoutMs": timeout_ms, "waitUntil": wait_until, "proxy": proxy, "browser": browser, "delayMs": delay_ms, "viewport": viewport, "links": links, "blockAds": block_ads})
        return await self._json("POST", "/v1/fetch", json=body, options=options)

    async def screenshot(
        self,
        url: str,
        full_page: bool = False,
        format: str = "png",
        quality: Optional[int] = None,
        selector: Optional[str] = None,
        viewport: Optional[Dict[str, int]] = None,
        wait_until: Optional[str] = None,
        delay_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        proxy: Optional[Proxy] = None,
        browser: Optional[Union[bool, JSON]] = None,
        block_ads: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> bytes:
        """A screenshot of any URL (fresh browser context each time): PNG or JPEG bytes."""
        body = clean({"url": url, "fullPage": full_page, "format": format, "quality": quality, "selector": selector, "viewport": viewport,
                      "waitUntil": wait_until, "delayMs": delay_ms, "timeoutMs": timeout_ms, "proxy": proxy, "browser": browser, "blockAds": block_ads})
        return await self._bytes("POST", "/v1/screenshot", json=body, options=options)

    async def pdf(
        self,
        url: str,
        paper: str = "A4",
        landscape: bool = False,
        print_background: bool = True,
        scale: Optional[float] = None,
        wait_until: Optional[str] = None,
        delay_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        proxy: Optional[Proxy] = None,
        browser: Optional[Union[bool, JSON]] = None,
        viewport: Optional[Dict[str, int]] = None,
        block_ads: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> bytes:
        """A PDF of any URL, printed like Chrome's "Save as PDF"."""
        body = clean({"url": url, "paper": paper, "landscape": landscape, "printBackground": print_background, "scale": scale, "waitUntil": wait_until,
                      "delayMs": delay_ms, "timeoutMs": timeout_ms, "proxy": proxy, "browser": browser, "viewport": viewport, "blockAds": block_ads})
        return await self._bytes("POST", "/v1/pdf", json=body, options=options)

    async def extract(
        self,
        url: Optional[str] = None,
        urls: Optional[Sequence[str]] = None,
        prompt: Optional[str] = None,
        schema: Optional[JSON] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        wait_until: Optional[str] = None,
        delay_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        proxy: Optional[Proxy] = None,
        browser: Optional[Union[bool, JSON]] = None,
        block_ads: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ExtractResult:
        """Structured data from pages: rendered in a real browser, then a model fills ``schema`` (JSON Schema) and/or
        follows ``prompt``. Returns ``{"data", "pages", "provider", "model", "usage", "ms"}``."""
        body = clean({"url": url, "urls": as_list(urls), "prompt": prompt, "schema": schema, "provider": provider, "model": model,
                      "waitUntil": wait_until, "delayMs": delay_ms, "timeoutMs": timeout_ms, "proxy": proxy, "browser": browser, "blockAds": block_ads})
        return await self._json("POST", "/v1/extract", json=body, options=options)

    async def search(
        self,
        query: str,
        limit: Optional[int] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        recency: Optional[str] = None,
        safe_search: Optional[str] = None,
        fetch: Optional[Union[bool, int]] = None,
        proxy: Optional[Proxy] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.SearchResponse:
        """Web search (the platform's provider, Brave): titles, URLs, snippets and dates. ``fetch=True`` (the top 3) or a
        number 0–5 also opens those pages in a sandboxed browser and returns them as Markdown (``content``). The same
        search within an hour is answered from the cache (``cached``, not counted). ``recency``: day, week, month or year;
        ``safe_search``: off, moderate or strict. The query text goes to the provider: keep secrets and personal data out
        of it. Raises SearchUnavailableError (503 ``search_unavailable``) when search is not set up."""
        body = clean({"query": query, "limit": limit, "country": country, "language": language, "recency": recency, "safeSearch": safe_search, "fetch": fetch, "proxy": proxy})
        return await self._json("POST", "/v1/search", json=body, options=options)

    # ----- usage and meta

    async def usage(self, from_: Optional[str] = None, to: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.Usage:
        """Usage and cost of the sessions created in a period (default: this month so far)."""
        return await self._json("GET", "/v1/usage", params={"from": from_, "to": to}, options=options)

    async def stats(self, days: int = 7, *, options: Optional[RequestOptions] = None) -> t.Stats:
        """Totals and per-day numbers for the last ``days`` days (UTC, including today)."""
        return await self._json("GET", "/v1/stats", params={"days": days}, options=options)

    async def pricing(self, *, options: Optional[RequestOptions] = None) -> t.Pricing:
        """The public price table and plans (no API key needed)."""
        return await self._json("GET", "/v1/pricing", options=options)

    async def openapi(self, *, options: Optional[RequestOptions] = None) -> JSON:
        """The OpenAPI 3.1 description of the API."""
        return await self._json("GET", "/v1/openapi.json", options=options)

    async def health(self, *, options: Optional[RequestOptions] = None) -> JSON:
        """``{"ok": True}`` when the API is up."""
        return await self._json("GET", "/healthz", options=options)


# ---------------------------------------------------------------- auth, project, API keys


class AsyncAuth:
    """Sign-up and console logins. Server-side code uses an API key instead of logging in."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def signup(
        self,
        email: str,
        password: str,
        *,
        accept_terms: bool,
        name: Optional[str] = None,
        options: Optional[RequestOptions] = None,
    ) -> t.SignupResponse:
        """Creates a user, a project on the Free plan and a first API key (in the response only). No API key needed.

        ``accept_terms=True`` says the user accepts the terms of service and acceptable use policy (400
        ``terms_not_accepted`` without it); ``name`` is optional."""
        body: Dict[str, Any] = {"email": email, "password": password, "acceptTerms": accept_terms}
        if name is not None:
            body["name"] = name
        return await self._c._json("POST", "/v1/auth/signup", json=body, options=options)

    async def login(self, email: str, password: str, *, options: Optional[RequestOptions] = None) -> t.LoginResponse:
        """Starts a console login: the cookie is kept by this client, so later calls act as that user."""
        return await self._c._json("POST", "/v1/auth/login", json={"email": email, "password": password}, options=options)

    async def logout(self, *, options: Optional[RequestOptions] = None) -> None:
        """Ends the console login."""
        await self._c._json("POST", "/v1/auth/logout", options=options)


class AsyncProject:
    """Project settings."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def trajectories(self, *, options: Optional[RequestOptions] = None) -> t.Trajectories:
        """The Trajectories program setting: ``{"enabled", "noticeSeenAt", "decidedBy"}`` (on by default; see the
        Terms of Service and Privacy Policy)."""
        return await self._c._json("GET", "/v1/project/trajectories", options=options)

    async def set_trajectories(self, enabled: bool, source: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.Trajectories:
        """Turns the Trajectories program on or off for this project (logged)."""
        return await self._c._json("PUT", "/v1/project/trajectories", json=clean({"enabled": enabled, "source": source}), options=options)

    async def settings(self, *, options: Optional[RequestOptions] = None) -> t.ProjectSettings:
        """The project's settings: ``captchaDefault`` is what new sessions and agent runs without a ``captcha`` option
        get; ``captchaDefaultEffective`` what they get now (the plan may no longer include solving)."""
        return await self._c._json("GET", "/v1/project/settings", options=options)

    async def set_settings(
        self,
        captcha_default: Optional[str] = None,
        default_model: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ProjectSettings:
        """Changes the fields you pass (``captcha_default="solve"`` needs a plan with CAPTCHA solving: 402 otherwise).
        ``default_model={"provider": "openai", "model": "gpt-6-luna"}`` is the model used when a request names none;
        ``default_model=None`` clears it."""
        body: Dict[str, Any] = clean({"captchaDefault": captcha_default})
        if default_model is not NOT_GIVEN:
            body["defaultModel"] = default_model  # None clears it
        return await self._c._json("PUT", "/v1/project/settings", json=body, options=options)

    async def model_keys(self, *, options: Optional[RequestOptions] = None) -> t.ModelKeys:
        """The four providers with the project's own-key state: ``{"keys": [{"provider", "use", "useEffective", "hasKey",
        "preview", ...}]}``. A key is never returned; ``preview`` is its last 4 characters."""
        return await self._c._json("GET", "/v1/project/model-keys", options=options)

    async def set_model_key(
        self,
        provider: t.Provider,
        key: Optional[str] = None,
        use: Optional[t.KeySource] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ModelKey:
        """Saves a provider key and/or the choice of whose key calls use (``use="project"`` needs a saved key,
        ``use="platform"`` a plan with ``platformModels``: 402 otherwise). The provider checks the key first (400
        ``invalid_model_key``). Runs on your own key have no model charge from Boxline."""
        return await self._c._json("PUT", f"/v1/project/model-keys/{provider}", json=clean({"key": key, "use": use}), options=options)

    async def delete_model_key(self, provider: t.Provider, *, options: Optional[RequestOptions] = None) -> None:
        """Deletes the provider's key; calls go back to the platform's key where the plan includes it."""
        await self._c._json("DELETE", f"/v1/project/model-keys/{provider}", options=options)


class AsyncApiKeys:
    """The project's API keys."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.ApiKey]:
        """Active keys, oldest first (the keys themselves are never shown again; ``prefix`` identifies them)."""
        return self._c._list("/v1/api-keys", {"limit": limit, "after": after}, lambda k: k, options)

    async def create(self, name: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.NewApiKey:
        """A new key; ``key`` is in this response only."""
        return await self._c._json("POST", "/v1/api-keys", json=clean({"name": name}), options=options)

    async def revoke(self, key_id: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._c._json("DELETE", f"/v1/api-keys/{seg(key_id)}", options=options)


# ---------------------------------------------------------------- sessions


class AsyncSessions:
    """Sessions: one isolated machine each, with a browser and/or a shell and a shared /workspace disk."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client
        self.files = AsyncSessionFiles(client)

    def _wrap(self, data: Any) -> "AsyncSession":
        return AsyncSession(self._c, data)

    async def _one(self, method: str, path: str, body: Any = None, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return self._wrap(await self._c._json(method, path, json=body, options=options))

    async def create(
        self,
        browser: bool = True,
        shell: bool = False,
        timeout: Optional[int] = None,
        keep_alive: Optional[bool] = None,
        viewport: Optional[Dict[str, int]] = None,
        user_metadata: Optional[JSON] = None,
        context: Optional[Union[str, JSON]] = None,
        persist_context: bool = False,
        record_session: Optional[bool] = None,
        setup: Optional[Sequence[str]] = None,
        proxy: Optional[Proxy] = None,
        captcha: Optional[str] = None,
        browser_options: Optional[JSON] = None,
        block_ads: Optional[bool] = None,
        cookie_banners: Optional[str] = None,
        extensions: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, Union[str, int, float, bool]]] = None,
        secrets: Optional[Sequence[str]] = None,
        idle_timeout: Optional[int] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> "AsyncSession":
        """Starts a session (an Idempotency-Key is sent, so a retry never starts a second one).

        ``timeout`` is the session's length in seconds (default 300). ``idle_timeout`` (opt-in, 30 to ``timeout``): the
        session ends (end reason ``idle``) after that many seconds without activity (CDP commands, live-view input,
        terminal keys, exec, files, actions and steps, scripts, agent steps and messages; an agent run working in it, or
        one that can still be continued, counts the whole time). ``context`` is a saved login's id (or
        ``{"id": ..., "persist": True}``) to start from its cookies and local storage; ``setup`` lists shell commands
        (package installs) run at start.

        ``proxy`` sends the session's traffic through a proxy:
        ``{"type": "residential", "country": "US", "state": "us_california", "city": "los_angeles"}``,
        ``{"type": "datacenter", "country": "DE"}`` or ``{"type": "custom", "server": "http://host:port",
        "username": ..., "password": ...}``; plus ``"ip": "sticky" | "rotating"`` and ``"scope": "browser" | "all"``
        (all = the shell uses it too). ``True`` means a residential US proxy. A list of rules picks a proxy per
        site: each rule may have a ``"domainPattern"`` (a regular expression tested against the host name), the
        first match wins, ``{"type": "none"}`` goes straight out, and no match goes straight out.

        ``captcha``: when a CAPTCHA waits for a person, ``"ask"`` (default) pauses agent runs and plain-English
        steps until someone solves it in the live view; ``"ignore"`` lets them carry on; ``"solve"`` has the
        platform try to solve it automatically and falls back to ``"ask"`` on failure. Either way the session's
        ``attention`` and its ``captcha`` events report it (see ``Session.wait_for_human``). Only enable ``"solve"``
        for sites you are authorised to automate.

        ``browser_options``: ``{"mode": "realistic"}`` puts the browser's clock and language on the proxy's
        country; ``{"locale": "de-DE", "timezone": "Europe/Berlin"}`` sets them yourself. It never changes what the
        browser reports about itself.

        ``block_ads=True`` refuses requests to ad and tracker sites inside the machine (``blocked_requests`` counts
        them); ``cookie_banners`` is ``"reject"`` (the default: "Reject all" / "Necessary only", never accept) or
        ``"off"``; ``extensions`` are uploaded extension ids (``extensions.upload``, at most 10, set at start only).
        An extension sees every page and every typed value in the session: use only ones you trust.

        ``env`` (needs ``shell=True``): variables for the session's shell, ``{"NAME": "value"}``: new terminals, exec,
        scripts and ``setup`` commands get them, and they are set again on every new machine (move, resume, recovery).
        Names like the shell's (not PATH, HOME or BOXLINE_*), at most 100, 64 KB together; the session shows the names
        only. ``secrets`` (needs ``shell=True``): project secrets exported into the shell as ``$NAME`` (scope "shell" or
        "all", or ``shell: True``; SecretNotAllowedError otherwise), kept in the machine's memory only and hidden in
        exec, script and terminal output. Anything that runs in the shell can read them: export only what you accept
        that for."""
        ctx = {"id": context, "persist": persist_context} if isinstance(context, str) else context
        body = clean(
            {
                "browser": browser_options if browser_options is not None else browser,
                "shell": shell,
                "timeout": timeout,
                "keepAlive": keep_alive,
                "viewport": viewport,
                "userMetadata": user_metadata,
                "context": ctx,
                "recordSession": record_session,
                "setup": as_list(setup),
                "proxy": proxy,
                "captcha": captcha,
                "blockAds": block_ads,
                "cookieBanners": cookie_banners,
                "extensions": as_list(extensions),
                "env": env,
                "secrets": as_list(secrets),
                "idleTimeout": idle_timeout,
            }
        )
        return await self._one("POST", "/v1/sessions", body, options)

    async def get(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return await self._one("GET", f"/v1/sessions/{seg(session_id)}", None, options)

    def list(
        self,
        status: Optional[Union[str, Sequence[str]]] = None,
        kind: Optional[str] = None,
        q: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        sort: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        offset: Optional[int] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncPager["AsyncSession"]:
        """Sessions, newest first unless ``sort`` says otherwise (``created_asc``, ``duration_desc``). ``status``: one or
        several (``["RUNNING", "PAUSED"]``); ``kind``: ``browser``, ``combined`` or ``shell``; ``q``: an id prefix or
        text in ``userMetadata``. Iterate for every session; the first page's ``total`` counts every match.
        ``offset`` is deprecated: use ``after``."""
        params = {"status": status, "kind": kind, "q": q, "from": from_, "to": to, "sort": sort, "limit": limit, "after": after, "offset": offset}
        return self._c._list("/v1/sessions", params, self._wrap, options)

    async def page(self, **filters: Any) -> AsyncPage["AsyncSession"]:
        """Deprecated: use ``list`` (its first page has ``total`` too)."""
        return await self.list(**filters)

    async def bulk(self, action: str, ids: Sequence[str], *, options: Optional[RequestOptions] = None) -> t.BulkResult:
        """Pause, resume or release 1 to 100 sessions at once (``action``: "release", "pause" or "resume"; ``ids`` are
        session ids; 30 calls per minute per project)."""
        return await self._c._json("POST", "/v1/sessions/bulk", json={"action": action, "ids": list(ids)}, options=options)

    async def update(
        self,
        session_id: str,
        keep_alive: Optional[bool] = None,
        user_metadata: Optional[JSON] = None,
        captcha: Optional[str] = None,
        browser: Optional[JSON] = None,
        proxy: Any = NOT_GIVEN,
        block_ads: Optional[bool] = None,
        cookie_banners: Optional[str] = None,
        idle_timeout: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> "AsyncSession":
        """Changes keepAlive, userMetadata, the captcha option, the browser settings, the proxy (``proxy=None`` removes
        it; a new one applies at once), ad blocking or cookie banners (both at once), or the idle timeout (seconds,
        counted from now; ``idle_timeout=None`` switches it off)."""
        body = clean({"keepAlive": keep_alive, "userMetadata": user_metadata, "captcha": captcha, "browser": browser, "blockAds": block_ads, "cookieBanners": cookie_banners})
        if proxy is not NOT_GIVEN:
            body["proxy"] = proxy
        if idle_timeout is not NOT_GIVEN:
            body["idleTimeout"] = idle_timeout
        return await self._one("PATCH", f"/v1/sessions/{seg(session_id)}", body, options)

    async def release(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Ends the session and deletes its machine."""
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/release", None, options)

    async def pause(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Saves cookies, storage, tabs and the workspace, then frees the machine (billing stops). Touching the session resumes it."""
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/pause", None, options)

    async def resume(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/resume", None, options)

    async def move(self, session_id: str, *, options: Optional[RequestOptions] = None) -> Dict[str, Any]:
        """Moves the live session to a fresh machine: ``{"session": Session, "timings": {...}, "shell": {...} | None}``
        (``shell``: where the shell continues, the exported variables that came along, and the processes that were
        stopped; None without a shell). Clients reconnect to the same connect URL."""
        r = await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/move", options=options)
        return {"session": self._wrap(r["session"]), "timings": r["timings"], "shell": r.get("shell")}

    async def extend(self, session_id: str, seconds: int, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Adds time (60–3600 s), up to the plan's maximum session length."""
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/extend", {"seconds": seconds}, options)

    async def rotate_proxy(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """A new IP for the session's proxy (sticky proxies keep one IP until this is called)."""
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/proxy/rotate", None, options)

    async def rotate_urls(self, session_id: str, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Revokes the session's connect, live and terminal URLs (e.g. one leaked) and returns it with fresh ones."""
        return await self._one("POST", f"/v1/sessions/{seg(session_id)}/rotate-urls", None, options)

    async def live(self, session_id: str, *, options: Optional[RequestOptions] = None) -> t.SessionUrls:
        """Fresh signed URLs: ``{"liveUrl", "terminalUrl", "connectUrl"}`` (treat them like passwords)."""
        return await self._c._json("GET", f"/v1/sessions/{seg(session_id)}/live", options=options)

    async def actions(self, session_id: str, actions: Union[ActionItem, Sequence[ActionItem]], timeout_ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> List[t.ActionResult]:
        """Runs browser actions in order, next to the browser, e.g. ``[{"action": "goto", "url": ...}, "click Sign in",
        {"action": "content"}]`` (a bare string is a plain-English step); stops at the first failure. Each result's
        ``text`` says what happened."""
        items = [actions] if isinstance(actions, (dict, str)) else list(actions)
        wait = STEP_TIMEOUT if any(isinstance(a, str) or a.get("action") == "step" for a in items) else None
        r = await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/actions", json=clean({"actions": items, "timeoutMs": timeout_ms}), options=options, timeout=wait)
        return r["results"]

    async def computer(
        self,
        session_id: str,
        action: JSON,
        max_width: Optional[int] = None,
        screenshot: Optional[bool] = None,
        format: Optional[str] = None,
        quality: Optional[int] = None,
        cursor: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ComputerResult:
        """Runs ONE computer-use action exactly as the model's tool gave it (Anthropic ``computer`` tool input, e.g.
        ``{"action": "left_click", "coordinate": [512, 300]}``, or one OpenAI ``computer_call`` action, e.g. ``{"type":
        "click", "x": 512, "y": 300, "button": "left"}``) on the session's page, and returns the screen after it. With
        ``max_width`` the screenshot is scaled down and the action's coordinates are read in its pixels (send the same
        value on every call). A failure in the page is ``ok: False``; input that cannot be mapped, or a point off the
        screen, raises (400 ``invalid_request`` / ``out_of_viewport``)."""
        body = {**action, **clean({"maxWidth": max_width, "screenshot": screenshot, "format": format, "quality": quality, "cursor": cursor})}
        return await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/computer", json=body, options=options)

    async def exec(
        self,
        session_id: str,
        command: str,
        timeout_ms: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: Optional[Union[str, bool]] = None,
        secrets: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ExecResult:
        """Runs a command (persistent bash by default: cd/export survive). ``shell=False`` runs it in a fresh process.
        ``cwd``, ``env`` and ``secrets`` (project secrets as environment variables, scope "shell" or "all") apply to this
        command only; the session's exported secrets and these are hidden in the output as ``%NAME%``."""
        body = clean({"command": command, "timeoutMs": timeout_ms, "cwd": cwd, "env": env, "shell": shell, "secrets": as_list(secrets)})
        return await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/exec", json=body, options=options, timeout=(timeout_ms or 120_000) / 1000 + 30 + SETUP_WAIT_S)

    async def exec_stream(
        self,
        session_id: str,
        command: str,
        timeout_ms: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: Optional[Union[str, bool]] = None,
        secrets: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> "AsyncExecStream":
        """Runs a command and streams its output (see ``ExecStream``); ``secrets`` as for ``exec``."""
        body = clean({"command": command, "timeoutMs": timeout_ms, "cwd": cwd, "env": env, "shell": shell, "secrets": as_list(secrets), "stream": True})
        return AsyncExecStream(await self._c._stream("POST", f"/v1/sessions/{seg(session_id)}/exec", json=body, options=options))

    async def run_script(
        self,
        session_id: str,
        code: str,
        env: Optional[Dict[str, str]] = None,
        timeout_ms: Optional[int] = None,
        ai: Optional[Dict[str, str]] = None,
        secrets: Optional[Sequence[str]] = None,
        login: Optional[bool] = None,
        allow_with_extensions: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> "AsyncExecStream":
        """Runs Playwright (JavaScript) code inside the session (needs a shell), streaming its output. In scope:
        ``page``, ``context``, ``browser``, ``env`` and the AI helpers ``step()``, ``extract()`` and
        ``useModel(model)`` / ``useModel(provider, model)``. A step or extract uses the call's own
        ``{provider, model}``, else the last ``useModel()``, else ``ai`` (``{"provider": ..., "model": ...}``), else
        the platform default.

        ``secrets``: project secrets the script's ``step()`` calls may use as ``%NAME%`` (scope "agent" or "all"); the
        values never enter the machine, and the grant is for this run only. ``login=True`` lets ``step()`` use the
        session's saved login details (``%login.username%``, ``%login.password%``, ``%login.otp%``). In a session with
        Chrome extensions both need ``allow_with_extensions=True`` (VariablesWithExtensionsError otherwise)."""
        body = clean({"code": code, "env": env, "timeoutMs": timeout_ms, "ai": ai, "secrets": as_list(secrets), "login": login, "allowWithExtensions": allow_with_extensions})
        return AsyncExecStream(await self._c._stream("POST", f"/v1/sessions/{seg(session_id)}/scripts/run", json=body, options=options))

    async def restart_shell(self, session_id: str, name: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> None:
        """Restarts the persistent shell (or the named one)."""
        await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/shell/restart", json=clean({"shell": name}), options=options)

    async def export_cookies(self, session_id: str, path: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.CookieFile:
        """Writes the browser's cookies as a Netscape cookie file in the workspace (for curl -b / wget)."""
        return await self._c._json("POST", f"/v1/sessions/{seg(session_id)}/browser/cookies/export", json=clean({"path": path}), options=options)

    def events(
        self,
        session_id: str,
        types: Optional[Sequence[str]] = None,
        after: Optional[Union[int, str]] = None,
        limit: Optional[int] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncPager[t.SessionEvent]:
        """Console, network, navigation, error, lifecycle, action, exec and captcha events, oldest first. Iterating
        stops once caught up; ``page.next_after`` is the ``after`` to poll with later."""
        return self._c._list(f"/v1/sessions/{seg(session_id)}/events", {"types": as_list(types), "after": after, "limit": limit}, lambda e: e, options)

    async def stream_events(self, session_id: str, after: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> AsyncIterator[t.SessionEvent]:
        """Events as they happen: first the backlog after ``after``, then live, until you stop iterating."""
        res = await self._c._stream("GET", f"/v1/sessions/{seg(session_id)}/events/stream", params={"after": after}, options=options)
        try:
            async for event in _sse(res.aiter_lines()):
                yield event  # type: ignore[misc]
        finally:
            await res.aclose()

    def pages(self, session_id: str, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.VisitedPage]:
        """Pages visited, grouped by tab and URL, in the order they were first visited."""
        return self._c._list(f"/v1/sessions/{seg(session_id)}/pages", {"limit": limit, "after": after}, lambda p: p, options)

    async def recording(self, session_id: str, *, options: Optional[RequestOptions] = None) -> t.Recording:
        """Replay frames kept while the session ran: ``{"frames": [{"index", "at", "url"}], "durationMs"}``."""
        return await self._c._json("GET", f"/v1/sessions/{seg(session_id)}/recording", options=options)

    async def recording_frame(self, session_id: str, index: int, *, options: Optional[RequestOptions] = None) -> bytes:
        """One replay frame as JPEG bytes."""
        return await self._c._bytes("GET", f"/v1/sessions/{seg(session_id)}/recording/frames/{int(index)}", options=options)


class AsyncSessionFiles:
    """Files in a session's /workspace (shared by the shell and the browser's downloads folder)."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def list(self, session_id: str, path: str = ".", *, options: Optional[RequestOptions] = None) -> List[t.FileEntry]:
        """The entries of a folder: ``[{"name", "type", "size", "mtime"}]``."""
        r = await self._c._json("GET", f"/v1/sessions/{seg(session_id)}/files", params={"path": path, "list": "1"}, options=options)
        return r["entries"]

    async def read(self, session_id: str, path: str, *, options: Optional[RequestOptions] = None) -> bytes:
        return await self._c._bytes("GET", f"/v1/sessions/{seg(session_id)}/files", params={"path": path}, options=options)

    async def read_text(self, session_id: str, path: str, encoding: str = "utf-8", *, options: Optional[RequestOptions] = None) -> str:
        return (await self.read(session_id, path, options=options)).decode(encoding)

    async def write(self, session_id: str, path: str, data: Union[str, bytes], *, options: Optional[RequestOptions] = None) -> t.FileRef:
        """Writes a file (folders are made as needed)."""
        content = data.encode() if isinstance(data, str) else data
        res = await self._c._send("PUT", f"/v1/sessions/{seg(session_id)}/files", content=content, params={"path": path}, options=options)
        return _parse(res)

    async def delete(self, session_id: str, path: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._c._json("DELETE", f"/v1/sessions/{seg(session_id)}/files", params={"path": path}, options=options)

    async def wait_for(self, session_id: str, pattern: str, timeout_ms: int = 30_000, *, options: Optional[RequestOptions] = None) -> t.FileRef:
        """Waits for a file matching a glob (e.g. ``downloads/*.csv``) that has finished writing."""
        return await self._c._json("GET", f"/v1/sessions/{seg(session_id)}/files/wait", params={"pattern": pattern, "timeoutMs": timeout_ms}, options=options, timeout=timeout_ms / 1000 + 30)


class AsyncExecStream:
    """Output of a streaming command or script. Iterate for ``(stream, text)`` pairs; ``result`` holds the exit
    information afterwards. ``aclose()`` (or leaving the ``async with`` block early) stops the command::

        proc = await s.exec_stream("npm install")
        async with proc:
            async for stream, text in proc:
                print(text, end="")
        print(proc.result["exitCode"])
    """

    def __init__(self, response: httpx.Response) -> None:
        self._res = response
        self._done = False
        self.result: Optional[t.ExecExit] = None

    async def __aiter__(self) -> AsyncIterator[Tuple[str, str]]:
        if self._done:
            return
        try:
            async for line in self._res.aiter_lines():
                msg = json_line(line)
                if msg is None:
                    continue
                if msg.get("type") == "exit":
                    self.result = msg  # type: ignore[assignment]
                elif msg.get("type") in ("stdout", "stderr"):
                    yield msg["type"], msg.get("data", "")
                elif msg.get("type") == "error":  # after the headers went out (e.g. the machine could not be reached)
                    raise make_error(msg.get("status") or 500, msg.get("code") or "internal", msg.get("message") or "the command failed", request_id=msg.get("requestId"))
                # "waiting" (for the session's setup) and "ping" lines only keep the connection open
        finally:
            self._done = True
            await self._res.aclose()

    async def wait(self) -> t.ExecExit:
        """Consumes the rest of the output and returns the exit information."""
        async for _ in self:
            pass
        return self.result or {"exitCode": None, "timedOut": False}

    async def aclose(self) -> None:
        self._done = True
        await self._res.aclose()

    async def __aenter__(self) -> "AsyncExecStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


# ---------------------------------------------------------------- one session


class AsyncSession:
    """A running (or finished) session. Attributes mirror the API: ``id``, ``status``, ``connect_url``,
    ``live_url``, ``terminal_url``, ``workspace_path`` (and any other field in snake_case); ``data`` has the full
    record. Leaving the ``async with`` block releases it."""

    def __init__(self, client: AsyncBoxline, data: t.SessionData) -> None:
        self._c = client
        self.data = data
        self.files = AsyncFiles(self)
        self.mouse = AsyncMouse(self)
        self.keyboard = AsyncKeyboard(self)

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def connect_url(self) -> Optional[str]:
        """WebSocket URL for Playwright/Puppeteer ``connect_over_cdp``. Treat it like a password."""
        return self.data.get("connectUrl")

    @property
    def live_url(self) -> Optional[str]:
        return self.data.get("liveUrl")

    @property
    def terminal_url(self) -> Optional[str]:
        return self.data.get("terminalUrl")

    @property
    def workspace_path(self) -> str:
        return self.data.get("workspacePath", "/workspace")

    def __getattr__(self, name: str) -> Any:
        data = self.__dict__.get("data") or {}
        key = camel(name)
        if key in data:
            return data[key]
        raise AttributeError(name)

    def __repr__(self) -> str:
        return f"Session(id={self.data.get('id')!r}, status={self.data.get('status')!r})"

    async def __aenter__(self) -> "AsyncSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.data.get("status") in ("RUNNING", "PAUSED"):
            try:
                await self.release()
            except BoxlineError:
                pass

    def _set(self, other: "AsyncSession") -> "AsyncSession":
        self.data = other.data
        return self

    # ----- lifecycle

    async def refresh(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return self._set(await self._c.sessions.get(self.id, options=options))

    async def release(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return self._set(await self._c.sessions.release(self.id, options=options))

    async def pause(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Saves cookies, storage, tabs and the workspace, then frees the machine (billing stops)."""
        return self._set(await self._c.sessions.pause(self.id, options=options))

    async def resume(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        return self._set(await self._c.sessions.resume(self.id, options=options))

    async def rotate_urls(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Makes the current connect/live/terminal URLs stop working (e.g. one leaked) and closes connections made
        with them; the session gets fresh URLs."""
        return self._set(await self._c.sessions.rotate_urls(self.id, options=options))

    async def move(self, *, options: Optional[RequestOptions] = None) -> t.MoveTimings:
        """Moves the live session to a fresh machine; returns the timings."""
        r = await self._c.sessions.move(self.id, options=options)
        self.data = r["session"].data
        return r["timings"]

    async def live(self, *, options: Optional[RequestOptions] = None) -> t.SessionUrls:
        """Fresh signed URLs (also stored in ``data``)."""
        urls = await self._c.sessions.live(self.id, options=options)
        self.data.update(urls)  # type: ignore[typeddict-item]
        return urls

    async def update(
        self,
        keep_alive: Optional[bool] = None,
        user_metadata: Optional[JSON] = None,
        captcha: Optional[str] = None,
        browser: Optional[JSON] = None,
        proxy: Any = NOT_GIVEN,
        block_ads: Optional[bool] = None,
        cookie_banners: Optional[str] = None,
        idle_timeout: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> "AsyncSession":
        """Changes keepAlive, userMetadata, the captcha option ("ask", "ignore" or "solve"), the browser options, the
        proxy (None removes it), ad blocking, cookie banners or the idle timeout (None switches it off)."""
        return self._set(
            await self._c.sessions.update(self.id, keep_alive, user_metadata, captcha, browser, proxy, block_ads, cookie_banners, idle_timeout, options=options)
        )

    async def set_proxy(self, proxy: Optional[Proxy], *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Sets, changes or (None) removes the session's proxy; new connections use it right away."""
        return await self.update(proxy=proxy, options=options)

    async def rotate_proxy(self, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """A new IP for the session's proxy (sticky sessions keep one IP until this is called)."""
        return self._set(await self._c.sessions.rotate_proxy(self.id, options=options))

    async def extend(self, seconds: int, *, options: Optional[RequestOptions] = None) -> "AsyncSession":
        """Adds time (60–3600 s), up to the plan's maximum session length."""
        return self._set(await self._c.sessions.extend(self.id, seconds, options=options))

    async def wait_for_human(self, timeout: float = 300, interval: float = 1) -> "AsyncSession":
        """Waits until no CAPTCHA is waiting for a person (someone solved it in the live view, or the page moved
        on). ``data["attention"]`` shows the one waiting. Raises CaptchaTimeoutError after ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            await self.refresh()
            attention = self.data.get("attention")
            if not attention or self.data.get("status") != "RUNNING":
                return self
            if time.monotonic() > deadline:
                raise CaptchaTimeoutError(408, "captcha_timeout", f"nobody solved the CAPTCHA on {attention.get('url')} in time")
            await _sleep(interval)

    # ----- browser

    async def actions(self, actions: Union[ActionItem, Sequence[ActionItem]], timeout_ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> List[t.ActionResult]:
        """Runs actions next to the browser in one call, e.g. ``[{"action": "goto", "url": ...}, "click Sign in",
        {"action": "content"}]`` (a bare string is a plain-English step)."""
        return await self._c.sessions.actions(self.id, actions, timeout_ms, options=options)

    async def _one(self, action: JSON, options: Optional[RequestOptions] = None) -> Any:
        r = (await self.actions(action, options=options))[0]
        if not r.get("ok"):
            code = r.get("code") or "action_failed"
            raise make_error(_ACTION_STATUS.get(code, 400), code, r.get("error") or "action failed")
        return r.get("value")

    async def step(
        self,
        instruction: str,
        variables: Optional[Dict[str, str]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        secrets: Optional[Sequence[str]] = None,
        allow_with_extensions: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.StepResult:
        """Runs one plain-English step ("click Sign in", "type %email% into the email field"). Variable values are
        filled in on the server and never sent to the model. Returns what was done and the Playwright ``code``.

        ``secrets``: project secrets usable as ``%NAME%`` (scope "agent" or "all"), each only on its own sites and in
        the shell only with ``shell: True``; the result never shows their values. In a session whose saved login has
        login details, ``%login.username%``, ``%login.password%`` and ``%login.otp%`` work too. In a session with Chrome
        extensions, secrets need ``allow_with_extensions=True`` (VariablesWithExtensionsError otherwise)."""
        body = {"action": "step", "instruction": instruction, "variables": variables, "provider": provider, "model": model, "secrets": as_list(secrets), "allowWithExtensions": allow_with_extensions}
        return await self._one(clean(body), options)

    async def extract(self, instruction: str, schema: Optional[JSON] = None, provider: Optional[str] = None, model: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.ExtractValue:
        """Structured data from the current page: ``{"data", "model", "usage"}``."""
        return await self._one(clean({"action": "extract", "instruction": instruction, "schema": schema, "provider": provider, "model": model}), options)

    async def goto(self, url: str, wait_until: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.GotoResult:
        return await self._one(clean({"action": "goto", "url": url, "waitUntil": wait_until}), options)

    async def click(
        self,
        selector: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
        button: Optional[str] = None,
        count: Optional[int] = None,
        modifiers: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> None:
        """Clicks an element (CSS selector) or a point (``x``, ``y``); ``count=2`` double-clicks, ``button="right"``
        right-clicks, ``modifiers=["Shift"]`` holds keys."""
        await self._one(clean({"action": "click", "selector": selector, "x": x, "y": y, "button": button, "count": count, "modifiers": as_list(modifiers)}), options)

    async def hover(self, selector: Optional[str] = None, x: Optional[float] = None, y: Optional[float] = None, *, options: Optional[RequestOptions] = None) -> None:
        """Moves the pointer over an element (CSS selector) or to a point."""
        await self._one(clean({"action": "hover", "selector": selector, "x": x, "y": y}), options)

    async def cursor(self, *, options: Optional[RequestOptions] = None) -> t.Point:
        """Where the pointer is on this tab (where the API last moved it; 0, 0 before any move)."""
        return await self._one({"action": "cursor"}, options)

    async def fill(self, selector: str, value: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._one({"action": "fill", "selector": selector, "value": value}, options)

    async def type(self, text: str, selector: Optional[str] = None, delay_ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> None:
        """Types text with the keyboard (into ``selector`` if given)."""
        await self._one(clean({"action": "type", "text": text, "selector": selector, "delayMs": delay_ms}), options)

    async def press(self, key: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._one({"action": "press", "key": key}, options)

    async def scroll(
        self,
        delta_y: float,
        x: Optional[float] = None,
        y: Optional[float] = None,
        delta_x: Optional[float] = None,
        modifiers: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> None:
        """Turns the wheel by ``delta_y`` pixels (and ``delta_x``), over ``x``/``y`` when given."""
        await self._one(clean({"action": "scroll", "deltaY": delta_y, "deltaX": delta_x, "x": x, "y": y, "modifiers": as_list(modifiers)}), options)

    async def wait(self, selector: Optional[str] = None, ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> None:
        """Waits for a selector, or a number of milliseconds."""
        await self._one(clean({"action": "wait", "selector": selector, "ms": ms}), options)

    async def select(self, selector: str, option: str, *, options: Optional[RequestOptions] = None) -> None:
        """Picks an option of a select element by its label or value."""
        await self._one({"action": "select", "selector": selector, "option": option}, options)

    async def elements(self, *, options: Optional[RequestOptions] = None) -> t.PageElements:
        """The page as a model sees it: title, URL, visible text and numbered interactive elements."""
        return await self._one({"action": "elements"}, options)

    async def evaluate(self, expression: str, *, options: Optional[RequestOptions] = None) -> Any:
        return await self._one({"action": "evaluate", "expression": expression}, options)

    async def content(self, format: str = "markdown", *, options: Optional[RequestOptions] = None) -> t.PageContent:
        """The current page as markdown, html or text: ``{"url", "title", "content"}``."""
        return await self._one({"action": "content", "format": format}, options)

    async def screenshot(
        self,
        full_page: bool = False,
        format: str = "png",
        quality: Optional[int] = None,
        max_width: Optional[int] = None,
        cursor: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> bytes:
        """A screenshot of the current tab: PNG or JPEG bytes. ``max_width`` scales it down; ``cursor=True`` draws the
        pointer."""
        v = await self._one(clean({"action": "screenshot", "fullPage": full_page, "format": format, "quality": quality, "maxWidth": max_width, "cursor": cursor}), options)
        return base64.b64decode(v["data"])

    async def computer(
        self,
        action: JSON,
        max_width: Optional[int] = None,
        screenshot: Optional[bool] = None,
        format: Optional[str] = None,
        quality: Optional[int] = None,
        cursor: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ComputerResult:
        """Runs ONE computer-use action as the model's tool gave it and returns the screen after it (see
        ``Sessions.computer``)."""
        return await self._c.sessions.computer(self.id, action, max_width, screenshot, format, quality, cursor, options=options)

    async def upload(self, selector: str, path: str, *, options: Optional[RequestOptions] = None) -> None:
        """Sets a file input to a file in the workspace (e.g. one written with ``files.write``)."""
        await self._one({"action": "upload", "selector": selector, "path": path}, options)

    async def tabs(self, *, options: Optional[RequestOptions] = None) -> t.TabList:
        return await self._one({"action": "tabs"}, options)

    async def new_tab(self, url: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> JSON:
        return await self._one(clean({"action": "newTab", "url": url}), options)

    async def switch_tab(self, index: int, *, options: Optional[RequestOptions] = None) -> None:
        await self._one({"action": "switchTab", "index": index}, options)

    async def close_tab(self, index: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._one(clean({"action": "closeTab", "index": index}), options)

    async def back(self, *, options: Optional[RequestOptions] = None) -> JSON:
        return await self._one({"action": "back"}, options)

    async def forward(self, *, options: Optional[RequestOptions] = None) -> JSON:
        return await self._one({"action": "forward"}, options)

    async def reload(self, *, options: Optional[RequestOptions] = None) -> JSON:
        return await self._one({"action": "reload"}, options)

    async def export_cookies(self, path: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.CookieFile:
        """Writes the browser's cookies as a Netscape cookie file in the workspace (for curl -b / wget)."""
        return await self._c.sessions.export_cookies(self.id, path, options=options)

    # ----- shell

    async def exec(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: Optional[Union[str, bool]] = None,
        secrets: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.ExecResult:
        """Runs a command (persistent bash by default: cd/export survive). Returns stdout, stderr, exitCode…
        ``shell=False`` runs it in a fresh process; ``secrets`` are project secrets for this command only."""
        return await self._c.sessions.exec(self.id, command, timeout_ms, cwd, env, shell, secrets, options=options)

    async def exec_stream(
        self,
        command: str,
        timeout_ms: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: Optional[Union[str, bool]] = None,
        secrets: Optional[Sequence[str]] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncExecStream:
        """Runs a command and streams its output while it runs::

            proc = await s.exec_stream("npm install")
            async with proc:
                async for stream, text in proc:
                    print(text, end="")
            print(proc.result["exitCode"])
        """
        return await self._c.sessions.exec_stream(self.id, command, timeout_ms, cwd, env, shell, secrets, options=options)

    async def run_script(
        self,
        code: str,
        env: Optional[Dict[str, str]] = None,
        timeout_ms: Optional[int] = None,
        ai: Optional[Dict[str, str]] = None,
        secrets: Optional[Sequence[str]] = None,
        login: Optional[bool] = None,
        allow_with_extensions: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncExecStream:
        """Runs Playwright (JavaScript) code inside the session, streaming its output (see ``Sessions.run_script``)."""
        return await self._c.sessions.run_script(self.id, code, env, timeout_ms, ai, secrets, login, allow_with_extensions, options=options)

    async def restart_shell(self, name: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._c.sessions.restart_shell(self.id, name, options=options)

    # ----- logs and recording

    def events(self, types: Optional[Sequence[str]] = None, after: Optional[Union[int, str]] = None, limit: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.SessionEvent]:
        """Console, network, navigation, error, lifecycle, action, exec and captcha events (oldest first)."""
        return self._c.sessions.events(self.id, types, after, limit, options=options)

    def stream_events(self, after: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> AsyncIterator[t.SessionEvent]:
        """Events as they happen (the backlog after ``after`` first), until you stop iterating."""
        return self._c.sessions.stream_events(self.id, after, options=options)

    def pages(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.VisitedPage]:
        """Pages visited in this session."""
        return self._c.sessions.pages(self.id, limit, after, options=options)

    async def recording(self, *, options: Optional[RequestOptions] = None) -> t.Recording:
        return await self._c.sessions.recording(self.id, options=options)

    async def recording_frame(self, index: int, *, options: Optional[RequestOptions] = None) -> bytes:
        """One replay frame as JPEG bytes."""
        return await self._c.sessions.recording_frame(self.id, index, options=options)


class AsyncMouse:
    """The mouse, in viewport CSS pixels (the session's viewport, 1280×720 by default). Straight lines only: ``steps``
    spreads a move over that many evenly spaced points. It acts on the page, never on the machine's desktop."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def move(self, x: float, y: float, steps: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> t.Point:
        return await self._s._one(clean({"action": "move", "x": x, "y": y, "steps": steps}), options)

    async def move_by(self, dx: float, dy: float, steps: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> t.Point:
        """Moves by ``dx``/``dy`` from where the pointer is."""
        return await self._s._one(clean({"action": "move", "dx": dx, "dy": dy, "steps": steps}), options)

    async def click(self, x: float, y: float, button: Optional[str] = None, count: Optional[int] = None, modifiers: Optional[Sequence[str]] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._one(clean({"action": "click", "x": x, "y": y, "button": button, "count": count, "modifiers": as_list(modifiers)}), options)

    async def down(self, button: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._one(clean({"action": "mouse_down", "button": button}), options)

    async def up(self, button: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._one(clean({"action": "mouse_up", "button": button}), options)

    async def drag(
        self,
        start: Optional[PointLike] = None,
        end: Optional[PointLike] = None,
        *,
        path: Optional[Sequence[PointLike]] = None,
        steps: Optional[int] = None,
        button: Optional[str] = None,
        modifiers: Optional[Sequence[str]] = None,
        options: Optional[RequestOptions] = None,
    ) -> JSON:
        """Presses at ``start``, moves to ``end`` (in ``steps`` points, default 10) and releases; each end is ``(x, y)``,
        ``{"x", "y"}`` or a selector. Or ``path=[(x, y), …]`` (2–200 points) to follow. Returns ``{"from", "to"}``."""
        body: JSON = {"action": "drag", **clean({"steps": steps, "button": button, "modifiers": as_list(modifiers)})}
        if path is not None:
            body["path"] = [_point(p) for p in path]
        else:
            if start is None or end is None:
                raise ValueError("drag needs start and end, or path")
            body["from"], body["to"] = _point(start), _point(end)
        return await self._s._one(body, options)


class AsyncKeyboard:
    """The keyboard. Key names are Playwright's (Enter, Tab, ArrowLeft, Control, Shift, Meta…); ctrl, cmd and Return
    work too."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def key(self, keys: Union[str, Sequence[str]], hold_ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> None:
        """A combination held together, ``"Control+A"`` or ``["Control", "A"]``; several separated by spaces run one after
        the other."""
        await self._s._one(clean({"action": "key", "keys": keys if isinstance(keys, str) else list(keys), "holdMs": hold_ms}), options)

    async def type(self, text: str, delay_ms: Optional[int] = None, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._one(clean({"action": "type", "text": text, "delayMs": delay_ms}), options)

    async def press(self, key: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._one({"action": "press", "key": key}, options)


class AsyncFiles:
    """Files in the session's workspace (shared by the shell and the browser's downloads folder)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(self, path: str = ".", *, options: Optional[RequestOptions] = None) -> List[t.FileEntry]:
        return await self._s._c.sessions.files.list(self._s.id, path, options=options)

    async def read(self, path: str, *, options: Optional[RequestOptions] = None) -> bytes:
        return await self._s._c.sessions.files.read(self._s.id, path, options=options)

    async def read_text(self, path: str, encoding: str = "utf-8", *, options: Optional[RequestOptions] = None) -> str:
        return await self._s._c.sessions.files.read_text(self._s.id, path, encoding, options=options)

    async def write(self, path: str, data: Union[str, bytes], *, options: Optional[RequestOptions] = None) -> t.FileRef:
        return await self._s._c.sessions.files.write(self._s.id, path, data, options=options)

    async def delete(self, path: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._s._c.sessions.files.delete(self._s.id, path, options=options)

    async def wait_for(self, pattern: str, timeout_ms: int = 30_000, *, options: Optional[RequestOptions] = None) -> t.FileRef:
        """Waits for a file matching a glob (e.g. ``downloads/*.csv``) that has finished writing."""
        return await self._s._c.sessions.files.wait_for(self._s.id, pattern, timeout_ms, options=options)


# ---------------------------------------------------------------- contexts


class AsyncContexts:
    """Saved logins: cookies and local storage to start sessions with (``context=id, persist_context=True`` fills one)."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def create(
        self,
        name: Optional[str] = None,
        *,
        from_session: Optional[str] = None,
        attach: Optional[bool] = None,
        options: Optional[RequestOptions] = None,
    ) -> t.Context:
        """A new saved login: empty, or with ``from_session`` holding that working session's current cookies and site
        storage (sign in there first, e.g. in its live view). ``attach=True`` also makes the session save to it from now
        on (at its checkpoints and when it ends); a session that already has a saved login refuses that (409
        ``conflict``). 413 ``context_too_large`` over 16 MB; PlanLimitError (402) past the plan's ``maxContexts`` or
        ``maxContextBytes``."""
        body = clean({"name": name, "fromSession": from_session, "attach": attach})
        return await self._c._json("POST", "/v1/contexts", json=body, options=options)

    async def get(self, context_id: str, *, options: Optional[RequestOptions] = None) -> t.Context:
        return await self._c._json("GET", f"/v1/contexts/{seg(context_id)}", options=options)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.Context]:
        """Newest first; the first page's ``total`` counts them all."""
        return self._c._list("/v1/contexts", {"limit": limit, "after": after}, lambda c: c, options)

    async def rename(self, context_id: str, name: str, *, options: Optional[RequestOptions] = None) -> t.Context:
        return await self._c._json("PATCH", f"/v1/contexts/{seg(context_id)}", json={"name": name}, options=options)

    async def delete(self, context_id: str, *, options: Optional[RequestOptions] = None) -> None:
        await self._c._json("DELETE", f"/v1/contexts/{seg(context_id)}", options=options)

    async def set_login(
        self,
        context_id: str,
        origin: str,
        username: str,
        password: str,
        totp_secret: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Context:
        """Keeps sign-in details on the saved login (plan feature ``loginDetails``), replacing earlier ones, sealed like
        secrets. ``origin``: the one site they may be typed on (``"https://example.com"`` or ``"https://*.example.com"``);
        ``totp_secret``: the site's 2FA setup key (base32) or an ``otpauth://totp/`` link from its QR code. Agent runs,
        steps and scripts' ``step()`` in a session started with this context get ``%login.username%``,
        ``%login.password%`` and ``%login.otp%`` (a TOTP code made when it is typed), only on ``origin``, never in shell
        commands, never shown to the model. Returns the context: its ``login`` shows ``{origin, username, hasPassword,
        hasTotp}``, never the password or the 2FA secret."""
        body = clean({"origin": origin, "username": username, "password": password, "totpSecret": totp_secret})
        return await self._c._json("PUT", f"/v1/contexts/{seg(context_id)}/login", json=body, options=options)

    async def update_login(
        self,
        context_id: str,
        origin: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Context:
        """Changes the login details you pass and keeps the rest (``set_login`` replaces them all); ``totp_secret=None``
        removes 2FA. A password never moves to another site on its own: a new ``origin`` needs ``password`` in the same
        call, and ``totp_secret`` (a new one or ``None``) when the login has 2FA (400 otherwise). A ``BoxlineError`` with
        code ``conflict`` (409) when the login changed meanwhile: send it again."""
        body = clean({"origin": origin, "username": username, "password": password})
        if totp_secret is not NOT_GIVEN:
            body["totpSecret"] = totp_secret
        return await self._c._json("PATCH", f"/v1/contexts/{seg(context_id)}/login", json=body, options=options)

    async def delete_login(self, context_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Removes the login details (the saved cookies and storage stay)."""
        await self._c._json("DELETE", f"/v1/contexts/{seg(context_id)}/login", options=options)


# ---------------------------------------------------------------- secrets


class AsyncSecrets:
    """Project secrets: write-only values the AI uses as ``%NAME%`` placeholders (``secrets=`` on agent runs, steps and
    scripts) and shells get as environment variables (``secrets=`` on ``sessions.create`` and ``exec``), depending on
    each secret's ``scope``. The value is never returned, logged or shown; every change and use is audited."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.Secret]:
        """The project's secrets, in name order, without their values."""
        return self._c._list("/v1/secrets", {"limit": limit, "after": after}, lambda s: s, options)

    async def create(
        self,
        name: str,
        value: str,
        description: Optional[str] = None,
        origins: Optional[Sequence[str]] = None,
        shell: Optional[bool] = None,
        scope: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Secret:
        """Stores a secret, sealed; the answer never has the value.

        ``name``: an environment variable name in capitals (``[A-Z_][A-Z0-9_]*``, at most 64; not PATH, HOME or
        BOXLINE_*/SANDBOXD_*/BASH_*); ``value``: 1 to 8000 characters. ``scope``: ``"agent"`` (default: only the AI, as
        ``%NAME%``), ``"shell"`` (only as an environment variable in shells and commands) or ``"all"``. ``shell=True``
        lets the AI use it in bash commands (and so export it). ``origins`` limits where the AI may type it
        (recommended for passwords). SecretExistsError for a name the project has (use ``update``), PlanLimitError
        beyond the plan's ``maxSecrets``. Not retried (the API takes no Idempotency-Key here)."""
        body = clean({"name": name, "value": value, "description": description, "origins": as_list(origins), "shell": shell, "scope": scope})
        return await self._c._json("POST", "/v1/secrets", json=body, options=options)

    async def get(self, name: str, *, options: Optional[RequestOptions] = None) -> t.Secret:
        return await self._c._json("GET", f"/v1/secrets/{seg(name)}", options=options)

    async def update(
        self,
        name: str,
        value: Optional[str] = None,
        description: Any = NOT_GIVEN,
        origins: Any = NOT_GIVEN,
        shell: Optional[bool] = None,
        scope: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Secret:
        """Changes the fields you pass; ``description=None`` clears it, ``origins=None`` allows any site. A running agent
        run keeps the value it started with; a session that exports the secret gets the new value on its next machine
        (move, resume, recovery)."""
        body = clean({"value": value, "shell": shell, "scope": scope})
        if description is not NOT_GIVEN:
            body["description"] = description
        if origins is not NOT_GIVEN:
            body["origins"] = None if origins is None else list(origins)
        return await self._c._json("PATCH", f"/v1/secrets/{seg(name)}", json=body, options=options)

    async def delete(self, name: str, *, options: Optional[RequestOptions] = None) -> None:
        """Deletes it; sessions that exported it no longer get it on their next machine."""
        await self._c._json("DELETE", f"/v1/secrets/{seg(name)}", options=options)

    def audit(self, name: Optional[str] = None, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.SecretAuditEntry]:
        """Changes to secrets and saved login details, and each use (once per session, command, run or script), newest
        first; ``name`` picks one secret (or a context id, for login details). Never values."""
        return self._c._list("/v1/secrets/audit", {"name": name, "limit": limit, "after": after}, lambda e: e, options)


# ---------------------------------------------------------------- crawl


class AsyncCrawl:
    """Crawls: follow links from a start URL in the background (robots.txt respected); poll with get() or wait()."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def start(
        self,
        url: str,
        max_pages: Optional[int] = None,
        max_depth: Optional[int] = None,
        same_host: Optional[bool] = None,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        format: Optional[str] = None,
        wait_until: Optional[str] = None,
        delay_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
        proxy: Optional[Proxy] = None,
        browser: Optional[Union[bool, JSON]] = None,
        block_ads: Optional[bool] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.CrawlJob:
        """Starts a crawl (an Idempotency-Key is sent, so a retry never starts a second one)."""
        body = clean({"url": url, "maxPages": max_pages, "maxDepth": max_depth, "sameHost": same_host, "include": as_list(include), "exclude": as_list(exclude),
                      "format": format, "waitUntil": wait_until, "delayMs": delay_ms, "timeoutMs": timeout_ms, "proxy": proxy, "browser": browser, "blockAds": block_ads})
        return await self._c._json("POST", "/v1/crawl", json=body, options=options)

    async def get(self, crawl_id: str, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.CrawlJob:
        """The job and one page of its pages (``limit=0`` for the job only); ``next`` is the cursor of the following pages."""
        return await self._c._json("GET", f"/v1/crawl/{seg(crawl_id)}", params={"limit": limit, "after": after}, options=options)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.CrawlJob]:
        """Jobs, newest first (without their pages)."""
        return self._c._list("/v1/crawl", {"limit": limit, "after": after}, lambda j: j, options)

    async def cancel(self, crawl_id: str, *, options: Optional[RequestOptions] = None) -> t.CrawlJob:
        return await self._c._json("POST", f"/v1/crawl/{seg(crawl_id)}/cancel", options=options)

    async def pages(self, crawl_id: str, limit: int = 100, *, options: Optional[RequestOptions] = None) -> AsyncIterator[t.CrawlPage]:
        """Every page crawled so far, in index order (``limit`` pages per request)."""
        after: Optional[str] = None
        while True:
            job = await self.get(crawl_id, limit=limit, after=after, options=options)
            for page in job["data"]:
                yield page
            after = job.get("next")
            if not after:
                return

    async def wait(self, crawl_id: str, poll: float = 1.0, timeout: float = 1800.0, *, options: Optional[RequestOptions] = None) -> t.CrawlJob:
        """Waits for the crawl to finish and returns the job with every page in ``data``."""
        deadline = time.monotonic() + timeout
        job = await self.get(crawl_id, limit=0, options=options)
        while job["status"] == "running":
            if time.monotonic() > deadline:
                raise BoxlineTimeoutError("the crawl did not finish in time")
            await _sleep(poll)
            job = await self.get(crawl_id, limit=0, options=options)
        job["data"] = [p async for p in self.pages(crawl_id, options=options)]
        job["next"] = None
        return job


# ---------------------------------------------------------------- webhooks


class AsyncWebhooks:
    """Webhook endpoints: signed HTTPS callbacks when something finishes or needs a person. Verify each delivery with
    ``verify_webhook(raw_body, header, secret)`` and drop event ids you have handled already."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def create(self, url: str, events: Sequence[str], description: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.NewWebhookEndpoint:
        """A new endpoint (public HTTPS only); ``secret`` ("whsec_…") is in this response only. ``events``: event
        types, or ``["*"]`` for all of them (those added later too)."""
        return await self._c._json("POST", "/v1/webhooks", json=clean({"url": url, "events": list(events), "description": description}), options=options)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.WebhookEndpoint]:
        """The project's endpoints, oldest first (never their secrets)."""
        return self._c._list("/v1/webhooks", {"limit": limit, "after": after}, lambda w: w, options)

    async def get(self, webhook_id: str, *, options: Optional[RequestOptions] = None) -> t.WebhookEndpoint:
        return await self._c._json("GET", f"/v1/webhooks/{seg(webhook_id)}", options=options)

    async def update(
        self,
        webhook_id: str,
        url: Optional[str] = None,
        events: Optional[Sequence[str]] = None,
        enabled: Optional[bool] = None,
        description: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.WebhookEndpoint:
        """Changes the URL, events or description (``None`` clears it), or switches it on or off (``enabled=True`` also
        forgets its failures)."""
        body = clean({"url": url, "events": as_list(events), "enabled": enabled})
        if description is not NOT_GIVEN:
            body["description"] = description
        return await self._c._json("PATCH", f"/v1/webhooks/{seg(webhook_id)}", json=body, options=options)

    async def delete(self, webhook_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Deletes the endpoint with its queue and delivery log."""
        await self._c._json("DELETE", f"/v1/webhooks/{seg(webhook_id)}", options=options)

    async def rotate_secret(self, webhook_id: str, *, options: Optional[RequestOptions] = None) -> t.NewWebhookEndpoint:
        """A new secret (in this response only); the old one keeps signing too for 24 hours (a second v1)."""
        return await self._c._json("POST", f"/v1/webhooks/{seg(webhook_id)}/rotate-secret", options=options)

    async def test(self, webhook_id: str, type: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.WebhookDelivery:
        """Sends a ``webhook.test`` event to this endpoint now, or with ``type`` a made-up sample of that event type
        (marked ``test: True``); one attempt, waits up to 12 s for the answer."""
        body = {"type": type} if type else None
        return await self._c._json("POST", f"/v1/webhooks/{seg(webhook_id)}/test", json=body, options=options)

    async def event_types(self, *, options: Optional[RequestOptions] = None) -> t.WebhookEventTypeList:
        """Every event type an endpoint can subscribe to, with its group and description (subscribe to ``"*"`` for
        all)."""
        return await self._c._json("GET", "/v1/webhooks/events", options=options)

    def deliveries(
        self,
        webhook_id: str,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncPager[t.WebhookDelivery]:
        """The endpoint's deliveries, newest first, each with its attempt ``history``; ``status``: pending, delivered or
        failed."""
        return self._c._list(f"/v1/webhooks/{seg(webhook_id)}/deliveries", {"status": status, "limit": limit, "after": after}, lambda d: d, options)

    async def retry_delivery(self, webhook_id: str, delivery_id: str, *, options: Optional[RequestOptions] = None) -> t.WebhookDelivery:
        """Sends a finished delivery again now, with the same event id and body (409 invalid_state while it is still
        queued, PayloadExpiredError after 7 days, WebhookDisabledError while the endpoint is switched off)."""
        return await self._c._json("POST", f"/v1/webhooks/{seg(webhook_id)}/deliveries/{seg(delivery_id)}/retry", options=options)


# ---------------------------------------------------------------- tasks

#: The task-run statuses of a run that has not finished (queued: a scheduled run waiting for its turn).
_TASK_RUN_OPEN = ["queued", "running", "paused"]


class AsyncTasks:
    """Tasks: saved agent runs (an instruction with ``%name%`` variables, an optional output schema, browser settings,
    a saved login and a model), run by hand or on a schedule. Every run is an agent run tagged with the task. Needs the
    plan's ``agentRuns``; the plan limits tasks and schedules switched on (PlanLimitError)."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def create(
        self,
        name: str,
        instruction: str,
        variables: Optional[Sequence[t.TaskVariable]] = None,
        output: Optional[t.OutputSchema] = None,
        browser: Optional[JSON] = None,
        saved_login: Optional[Union[str, JSON]] = None,
        model: Optional[Dict[str, str]] = None,
        max_steps: Any = NOT_GIVEN,
        notify_on_failure: Optional[str] = None,
        schedule: Optional[JSON] = None,
        secrets: Optional[Sequence[str]] = None,
        max_cost_usd: Optional[float] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Task:
        """Saves a task (an Idempotency-Key is sent, so a retry never saves it twice).

        ``instruction`` has ``%name%`` where a variable goes. ``variables``: up to 50 ``{"name", "default", "description"}``,
        or ``{"name", "secret": True, "origins", "shell"}`` for a value that is never stored (it comes with every run,
        and the model sees only ``%name%``). ``output``: a JSON Schema every run's answer must match (see
        ``agent.run``). ``browser``: the settings of each run's own session, with the API's keys (``{"proxy": {"type":
        "residential", "country": "GB"}, "blockAds": True, "locale": "en-GB"}``). ``saved_login``: a context id, or
        ``{"id", "persist"}``. ``model``: ``{"provider", "model"}`` from ``agent.models()``. ``max_steps``: 1-1000
        (default 30 when not passed); ``max_steps=None`` means no step limit. ``max_cost_usd``: each run's money budget
        (0.01-100 USD, as on ``agent.run``). ``browser`` also takes ``timeout`` and ``idleTimeout`` for each run's own
        session. ``schedule``: ``{"cron": "0 9 * * MON-FRI", "timezone": "Europe/London", "variables": {...},
        "enabled": True}``: at most every 5 minutes, on the plan's ``schedules`` (PlanLimitError beyond). ``secrets``:
        project secret names (scope "agent" or "all") each run gets as ``%NAME%``, as on ``agent.run``; a scheduled
        task may use them (secret variables cannot be scheduled)."""
        body = clean(
            {
                "name": name,
                "instruction": instruction,
                "variables": list(variables) if variables is not None else None,
                "output": output,
                "browser": browser,
                "savedLogin": saved_login,
                "model": model,
                "notifyOnFailure": notify_on_failure,
                "schedule": schedule,
                "secrets": list(secrets) if secrets is not None else None,
                "maxCostUsd": max_cost_usd,
            }
        )
        if max_steps is not NOT_GIVEN:
            body["maxSteps"] = max_steps  # None: no step limit
        return await self._c._json("POST", "/v1/tasks", json=body, options=options)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.Task]:
        """The project's tasks, newest first."""
        return self._c._list("/v1/tasks", {"limit": limit, "after": after}, lambda x: x, options)

    async def get(self, task_id: str, *, options: Optional[RequestOptions] = None) -> t.Task:
        return await self._c._json("GET", f"/v1/tasks/{seg(task_id)}", options=options)

    async def update(
        self,
        task_id: str,
        name: Optional[str] = None,
        instruction: Optional[str] = None,
        variables: Optional[Sequence[t.TaskVariable]] = None,
        output: Any = NOT_GIVEN,
        browser: Any = NOT_GIVEN,
        saved_login: Any = NOT_GIVEN,
        model: Any = NOT_GIVEN,
        max_steps: Any = NOT_GIVEN,
        notify_on_failure: Any = NOT_GIVEN,
        schedule: Any = NOT_GIVEN,
        secrets: Any = NOT_GIVEN,
        max_cost_usd: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.Task:
        """Changes the fields you pass; ``None`` removes ``output``, ``browser``, ``saved_login``, ``model``,
        ``max_cost_usd``, ``notify_on_failure``, ``schedule`` or ``secrets`` (the whole new list of secret names);
        ``max_steps=None`` means no step limit.
        ``schedule`` fields are merged into the current schedule (``schedule={"enabled": False}`` pauses it); switching it
        on, or changing cron or timezone, counts from now."""
        body = clean({"name": name, "instruction": instruction, "variables": list(variables) if variables is not None else None})
        for key, value in (
            ("output", output),
            ("browser", browser),
            ("savedLogin", saved_login),
            ("model", model),
            ("maxSteps", max_steps),
            ("notifyOnFailure", notify_on_failure),
            ("schedule", schedule),
            ("secrets", list(secrets) if secrets is not NOT_GIVEN and secrets is not None else secrets),
            ("maxCostUsd", max_cost_usd),
        ):
            if value is not NOT_GIVEN:
                body[key] = value
        return await self._c._json("PATCH", f"/v1/tasks/{seg(task_id)}", json=body, options=options)

    async def delete(self, task_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Deletes the task with its run history (its agent runs stay, and runs in progress go on)."""
        await self._c._json("DELETE", f"/v1/tasks/{seg(task_id)}", options=options)

    async def run(
        self,
        task_id: str,
        variables: Optional[Dict[str, Union[str, int, float, bool]]] = None,
        session_id: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.TaskRun:
        """Runs the task now and returns its task run (status running) right away; ``wait_for_run`` waits for the result.
        Plain values are written into the instruction; secret ones must come with every run and go in as agent
        variables (the model sees only ``%name%``). MissingVariablesError when a variable without a default has no
        value. ``session_id``: work in that session (its own settings apply). Counts as an agent run (rate, plan, spend
        cap). An Idempotency-Key is sent, so a retry never starts a second run."""
        body = clean({"variables": variables, "sessionId": session_id})
        return await self._c._json("POST", f"/v1/tasks/{seg(task_id)}/runs", json=body, options=options)

    def runs(
        self,
        task_id: str,
        status: Optional[Union[str, Sequence[str]]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> AsyncPager[t.TaskRun]:
        """The task's runs, newest first: by hand, on the schedule, and skipped or missed times (kept 30 days).
        ``status``: one or more of queued, running, paused, completed, failed, canceled, skipped, missed."""
        return self._c._list(f"/v1/tasks/{seg(task_id)}/runs", {"status": status, "limit": limit, "after": after}, lambda r: r, options)

    async def wait_for_run(
        self,
        task_id: Union[str, t.TaskRun],
        task_run_id: Optional[str] = None,
        poll: float = 1.0,
        timeout: float = 1800.0,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.TaskRun:
        """Polls until the task run has finished (completed, failed or canceled; a paused run keeps waiting for a
        person, a queued one for its turn) and returns it with its result. Pass the run that ``run`` returned, or the
        task's and the run's ids::

            run = bx.tasks.run(task["id"], variables={"category": "Poetry"})
            done = bx.tasks.wait_for_run(run)          # done["result"]: the JSON answer with an output schema

        NotFoundError when the run is not in the task's history; BoxlineTimeoutError after ``timeout`` seconds."""
        if isinstance(task_id, dict):
            task_id, task_run_id = task_id["taskId"], task_id["id"]
        if not task_run_id:
            raise TypeError("wait_for_run needs the task run: wait_for_run(run) or wait_for_run(task_id, task_run_id)")
        deadline = time.monotonic() + timeout
        while True:
            # There is no GET for one task run: the unfinished ones are a short list, and a run missing from it has finished.
            is_open = False
            async for r in self.runs(task_id, status=_TASK_RUN_OPEN, limit=100, options=options):
                if r["id"] == task_run_id:
                    is_open = True
                    break
            if not is_open:
                async for r in self.runs(task_id, limit=100, options=options):
                    if r["id"] == task_run_id:
                        return r
                raise NotFoundError(404, ErrorCode.NOT_FOUND, f"task run {task_run_id} is not in the run history of task {task_id}")
            if time.monotonic() > deadline:
                raise BoxlineTimeoutError("the task run did not finish in time")
            await _sleep(poll)


# ---------------------------------------------------------------- extensions


class AsyncExtensions:
    """Chrome extensions (Manifest V3) to start sessions with (``extensions=[id]``; plan feature ``extensions``). An
    extension sees every page and every typed value in the sessions that use it: upload only extensions you trust."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def upload(self, zip: Union[bytes, bytearray, str, "os.PathLike[str]"], *, options: Optional[RequestOptions] = None) -> t.Extension:
        """Uploads an unpacked extension as a zip, at most 10 MB: its bytes, or a file path. It is checked before it is
        stored (InvalidExtensionError says why; PayloadTooLargeError over 10 MB; LimitReachedError beyond 100
        extensions). An Idempotency-Key is sent, so a retry never stores it twice."""
        if isinstance(zip, (bytes, bytearray)):
            content = bytes(zip)
        else:
            with open(os.fspath(zip), "rb") as f:
                content = f.read()
        headers = {"content-type": "application/zip", **((options or {}).get("headers") or {})}
        res = await self._c._send("POST", "/v1/extensions", content=content, options={**(options or {}), "headers": headers})
        return _parse(res)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.Extension]:
        """The project's extensions, newest first."""
        return self._c._list("/v1/extensions", {"limit": limit, "after": after}, lambda e: e, options)

    async def get(self, extension_id: str, *, options: Optional[RequestOptions] = None) -> t.Extension:
        return await self._c._json("GET", f"/v1/extensions/{seg(extension_id)}", options=options)

    async def delete(self, extension_id: str, *, options: Optional[RequestOptions] = None) -> None:
        """Deletes it; sessions already running with it keep it until they move or resume."""
        await self._c._json("DELETE", f"/v1/extensions/{seg(extension_id)}", options=options)


# ---------------------------------------------------------------- agent


class AsyncAgent:
    """Agent runs: a model (Claude or GPT) drives the session's browser and shell to finish a task."""

    def __init__(self, client: AsyncBoxline) -> None:
        self._c = client

    async def models(self, *, options: Optional[RequestOptions] = None) -> t.AgentModels:
        """Providers and models you can choose, with prices and whether each is configured on the server."""
        return await self._c._json("GET", "/v1/agent/models", options=options)

    async def run(
        self,
        task: str,
        session_id: Optional[str] = None,
        browser: Optional[Union[bool, JSON]] = None,
        shell: Optional[bool] = None,
        max_steps: Any = NOT_GIVEN,
        effort: Optional[str] = None,
        keep_session: Optional[bool] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        proxy: Optional[Proxy] = None,
        captcha: Optional[str] = None,
        variables: Optional[Dict[str, t.AgentVariable]] = None,
        mode: Optional[str] = None,
        block_ads: Optional[bool] = None,
        cookie_banners: Optional[str] = None,
        extensions: Optional[Sequence[str]] = None,
        allow_with_extensions: Optional[bool] = None,
        output: Optional[t.OutputSchema] = None,
        secrets: Optional[Sequence[str]] = None,
        context: Optional[Union[str, JSON]] = None,
        timeout: Optional[int] = None,
        idle_timeout: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        max_consecutive_errors: Optional[int] = None,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.AgentRunStarted:
        """Starts a run and returns ``{"id", "status", "sessionId", "provider", "model"}`` right away (an
        Idempotency-Key is sent, so a retry never starts a second run). ``proxy`` and ``captcha`` ("ask", "ignore" or
        "solve") apply to the run's own session (see ``sessions.create``). ``browser`` is False for a run without
        one, or the session's browser options, e.g. ``{"mode": "realistic"}``.

        ``variables`` are secrets and other values the agent may type as ``%name%`` placeholders (e.g. a task
        "sign in with %email% and %password%"): ``{"email": "me@example.com", "password": {"value": "...",
        "origins": ["https://example.com"]}, "token": {"value": "...", "shell": True}}``. The model is told the
        names only. A value is filled in only where the model types text (browser_type) or picks an option
        (browser_select), never into URLs, selectors or keys; shell commands only with ``"shell": True``.
        ``origins`` limits typing it to fields on those sites (scheme, host and port; ``"https://*.example.com"`` for
        subdomains), recommended for passwords. Values are replaced by their placeholder in everything the model
        sees and the run stores, and kept in memory for the run only. Up to 50; names are letters, digits and ``_``
        (not starting with a digit, 64 characters at most); values up to 8000 characters and 64 KB together
        (shorter than 3 characters are filled in but not hidden).

        ``mode="computer"``: the model drives the page with its provider's own computer-use tool on screenshots (models
        with ``supportsComputerUse`` in ``agent.models()``; 400 otherwise, or without a browser). Tool and handover steps
        carry the model's ``thought``.

        ``block_ads``, ``cookie_banners`` and ``extensions`` apply to the run's own session. ``variables`` in a session
        with extensions (which can read every typed value) are refused (VariablesWithExtensionsError) unless
        ``allow_with_extensions=True``.

        ``output`` (structured output): a JSON Schema the answer must match, e.g. ``{"type": "object", "properties":
        {"title": {"type": "string"}}, "required": ["title"]}``. The finished run's ``result`` is then the JSON answer
        (a dict, list, …) and ``resultText`` a one-sentence summary; an answer that still does not match after one
        repair try fails the run with ``errorCode`` "output_invalid". A schema that is not valid, or over 32 KB, is a 400.

        ``secrets``: project secrets as ``%NAME%`` placeholders, exactly like ``variables``, each with the secret's own
        ``origins`` and ``shell`` rule (scope "agent" or "all"; SecretNotAllowedError for scope "shell"). An explicit
        variable with the same name wins. ``context``: for the run's own session, a saved login to start with (its id,
        or ``{"id", "persist"}``); with login details the run also gets ``%login.username%``, ``%login.password%`` and
        ``%login.otp%`` on the login's site.

        The run's ``steps`` include ``handover``/``handback`` steps and, for a CAPTCHA pause, ``captcha`` steps:
        ``state`` "solving" or "waiting" (with ``kind``, ``host`` and, when waiting, ``reason``), then "solved"
        with ``by`` ("auto" or "person") and ``ms``; messages you sent (``send_message``) are ``message`` steps.

        Limits: ``max_steps`` (1-1000, default 30 when not passed; ``max_steps=None`` means no step limit: the run goes
        until it is done or its session's time ends it), ``max_cost_usd`` (0.01-100 USD of model cost, checked before
        each model call; default none), ``max_consecutive_errors`` (tool errors in a row, 1-20, default 5), and the same
        call with the same result 5 times. At one of these the run ends with ``errorCode`` ``max_steps``, ``max_cost``,
        ``too_many_errors`` or ``no_progress``, ``resultText`` says what is done and what is left, and ``continuable``
        says until when ``continue_run`` can carry it on (its own session is kept that long). ``timeout`` (seconds) and
        ``idle_timeout`` are for the run's own session (default 1800, or the plan's maximum when shorter; not with
        ``session_id``); a run stops with ``session_timeout`` when its session's time ends."""
        body = clean(
            {
                "task": task,
                "sessionId": session_id,
                "browser": browser,
                "shell": shell,
                "effort": effort,
                "keepSession": keep_session,
                "provider": provider,
                "model": model,
                "proxy": proxy,
                "captcha": captcha,
                "variables": variables,
                "mode": mode,
                "blockAds": block_ads,
                "cookieBanners": cookie_banners,
                "extensions": as_list(extensions),
                "allowWithExtensions": allow_with_extensions,
                "output": output,
                "secrets": as_list(secrets),
                "context": {"id": context} if isinstance(context, str) else context,
                "timeout": timeout,
                "idleTimeout": idle_timeout,
                "maxCostUsd": max_cost_usd,
                "maxConsecutiveErrors": max_consecutive_errors,
            }
        )
        if max_steps is not NOT_GIVEN:
            body["maxSteps"] = max_steps  # None: no step limit
        return await self._c._json("POST", "/v1/agent/runs", json=body, options=options)

    async def get(self, run_id: str, *, options: Optional[RequestOptions] = None) -> t.AgentRun:
        return await self._c._json("GET", f"/v1/agent/runs/{seg(run_id)}", options=options)

    def list(self, limit: Optional[int] = None, after: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> AsyncPager[t.AgentRun]:
        """Runs, newest first."""
        return self._c._list("/v1/agent/runs", {"limit": limit, "after": after}, lambda r: r, options)

    async def takeover(self, run_id: str, reason: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.AgentRun:
        """Take the browser from the agent; it finishes its current action, then waits. RunNotLiveError when its server stopped."""
        return await self._c._json("POST", f"/v1/agent/runs/{seg(run_id)}/takeover", json=clean({"reason": reason}), options=options)

    async def hand_back(self, run_id: str, note: Optional[str] = None, *, options: Optional[RequestOptions] = None) -> t.AgentRun:
        """Give the browser back; the agent reads ``note`` before it continues. RunNotLiveError when its server stopped: continue_run."""
        return await self._c._json("POST", f"/v1/agent/runs/{seg(run_id)}/handback", json=clean({"note": note}), options=options)

    async def cancel(self, run_id: str, *, options: Optional[RequestOptions] = None) -> t.AgentRun:
        """Stops the run for good."""
        return await self._c._json("POST", f"/v1/agent/runs/{seg(run_id)}/cancel", options=options)

    async def continue_run(
        self,
        run_id: str,
        max_steps: Any = NOT_GIVEN,
        instruction: Optional[str] = None,
        variables: Optional[Dict[str, Union[str, Dict[str, str]]]] = None,
        max_cost_usd: Any = NOT_GIVEN,
        *,
        options: Optional[RequestOptions] = None,
    ) -> t.AgentRun:
        """Continues a run that stopped at one of its limits (``errorCode`` ``max_steps``, ``max_cost``,
        ``too_many_errors`` or ``no_progress``) while its ``continuable`` is set: a new run in the same session with the
        same model, mode, output schema, secrets and saved login, and a compact record of what the previous run did.
        Returns the new run (``continuedFrom`` links back); ``wait`` and ``stream`` work with it like any run.

        ``max_steps``: 1-1000 (default: the run's own); ``max_steps=None`` means no step limit. ``max_cost_usd``: the new
        run's own budget (default: the run's own; ``None``: none). ``instruction``: an extra note for the model (at most
        2000 characters). ``variables``: the original run's variables again (their values are never stored), as text or
        ``{"value": ...}``; they keep their sites and shell rule (MissingVariablesError when one is missing).
        NotContinuableError: it did not stop at a limit, was continued already, or its window passed. An Idempotency-Key
        is sent, so a retry never starts a second run."""
        body: Dict[str, Any] = clean({"instruction": instruction, "variables": variables})
        if max_steps is not NOT_GIVEN:
            body["maxSteps"] = max_steps
        if max_cost_usd is not NOT_GIVEN:
            body["maxCostUsd"] = max_cost_usd
        return await self._c._json("POST", f"/v1/agent/runs/{seg(run_id)}/continue", json=body, options=options)

    async def send_message(self, run_id: str, text: str, *, options: Optional[RequestOptions] = None) -> t.AgentMessageSent:
        """Tells a working run something (1-2000 characters) without taking the browser: the agent reads it at its next
        step (a model call or tool under way is not interrupted), and it shows as a ``message`` step. A run waiting for
        your help (``ask_user_for_help``) takes it as the answer and goes on. At most 50 per run (TooManyMessagesError);
        a BoxlineError with code ``invalid_state`` once the run has finished."""
        return await self._c._json("POST", f"/v1/agent/runs/{seg(run_id)}/messages", json={"text": text}, options=options)

    async def stream(self, run_id: str, *, options: Optional[RequestOptions] = None) -> AsyncIterator[t.AgentRunEvent]:
        """Yields the run's events as they happen: its steps so far, then new steps, the model's ``thought`` as soon as
        its reply arrives, ``status`` changes, live shell ``exec``/``output``, and a final ``done`` event, after which the
        iteration ends."""
        res = await self._c._stream("GET", f"/v1/agent/runs/{seg(run_id)}/events", options=options)
        try:
            async for event in _sse(res.aiter_lines()):
                yield event
                if event.get("type") == "done":
                    return
        finally:
            await res.aclose()

    async def wait(self, run_id: str, poll: float = 1.0, timeout: float = 1800.0, *, options: Optional[RequestOptions] = None) -> t.AgentRun:
        """Polls until the run finishes (a paused run keeps waiting for the user)."""
        deadline = time.monotonic() + timeout
        while True:
            run = await self.get(run_id, options=options)
            if run["status"] not in ("running", "paused"):
                return run
            if time.monotonic() > deadline:
                raise BoxlineTimeoutError("the agent run did not finish in time")
            await _sleep(poll)
