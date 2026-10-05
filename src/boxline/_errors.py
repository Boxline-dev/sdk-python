"""Errors. Every failed call raises a :class:`BoxlineError` (or a subclass) with the HTTP ``status``, the API's
stable snake_case ``code``, a ``message`` for people and the ``request_id`` to quote when asking for support."""

from __future__ import annotations

import email.utils
import time
from typing import Any, Dict, Optional, Type

import httpx


class ErrorCode:
    """Stable error codes the SDK gives its own class to (docs/CONTRACT.md "Errors")."""

    RATE_LIMITED = "rate_limited"
    CONCURRENCY_LIMIT = "concurrency_limit"
    FEATURE_NOT_IN_PLAN = "feature_not_in_plan"
    PROJECT_SUSPENDED = "project_suspended"
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    IDEMPOTENCY_IN_PROGRESS = "idempotency_in_progress"
    INVALID_CURSOR = "invalid_cursor"
    CAPTCHA_TIMEOUT = "captcha_timeout"
    PAGE_UNREACHABLE = "page_unreachable"
    PAGE_TIMEOUT = "page_timeout"
    NOT_A_WEB_PAGE = "not_a_web_page"
    MODEL_REFUSED = "model_refused"
    SEARCH_UNAVAILABLE = "search_unavailable"
    OUT_OF_VIEWPORT = "out_of_viewport"
    WEBHOOK_URL_NOT_ALLOWED = "webhook_url_not_allowed"
    WEBHOOKS_UNAVAILABLE = "webhooks_unavailable"
    WEBHOOK_DISABLED = "webhook_disabled"
    PAYLOAD_EXPIRED = "payload_expired"
    #: A delivery's errorCode (never an HTTP error): the project already had 10,000 deliveries waiting.
    QUEUE_FULL = "queue_full"
    INVALID_SIGNATURE = "invalid_signature"
    VARIABLES_WITH_EXTENSIONS = "variables_with_extensions"
    EXTENSION_DENIED = "extension_denied"
    CROSS_SITE_REQUEST = "cross_site_request"
    INVALID_EXTENSION = "invalid_extension"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    LIMIT_REACHED = "limit_reached"
    MISSING_VARIABLES = "missing_variables"
    PLAN_LIMIT = "plan_limit"
    TOO_MANY_CREDENTIAL_VALUES = "too_many_credential_values"
    CREDENTIAL_EXISTS = "credential_exists"
    CREDENTIAL_NOT_FOUND = "credential_not_found"
    CREDENTIAL_NOT_FOR_AI = "credential_not_for_ai"
    CREDENTIAL_NOT_FOR_SHELL = "credential_not_for_shell"
    #: 408 for ``boxline-otp``, an action result's code in a list: no code or link arrived in time.
    CREDENTIAL_CODE_TIMEOUT = "credential_code_timeout"
    CREDENTIAL_LINK_WRONG_SITE = "credential_link_wrong_site"
    #: An action result's code: the ``login`` action could not sign in.
    CREDENTIAL_LOGIN_FAILED = "credential_login_failed"
    #: An action result's code: the ``login`` action ran out of time and its run was canceled.
    CREDENTIAL_LOGIN_TIMEOUT = "credential_login_timeout"
    CODE_URL_NOT_ALLOWED = "code_url_not_allowed"
    MACHINE_TOO_OLD = "machine_too_old"
    #: An agent or task run's errorCode (never an HTTP error): the answer did not match the run's output schema after
    #: the repair try (the run's ``error`` lists the problems).
    OUTPUT_INVALID = "output_invalid"
    #: Agent-run errorCodes (never HTTP errors) for a run that stopped at one of its limits and can be continued
    #: (``agent.continue_run``) while its ``continuable`` is set: its max_steps, its max_cost_usd, max_consecutive_errors
    #: tool errors in a row, or the same call with the same result 5 times in a row.
    MAX_STEPS = "max_steps"
    MAX_COST = "max_cost"
    TOO_MANY_ERRORS = "too_many_errors"
    NO_PROGRESS = "no_progress"
    #: Agent-run errorCode (continuable like the limits): the API server running the loop stopped (a restart, a deploy)
    #: while its session went on; ``agent.continue_run`` picks it up in the same browser.
    SERVER_RESTARTED = "server_restarted"
    #: Agent-run errorCodes: its session reached its time limit, or ended another way, while it worked (not continuable).
    SESSION_TIMEOUT = "session_timeout"
    SESSION_ENDED = "session_ended"
    #: 409: continue_run on a run that did not stop at a limit, was continued already, or whose window passed.
    NOT_CONTINUABLE = "not_continuable"
    #: 409: send_message after 50 messages to one run.
    TOO_MANY_MESSAGES = "too_many_messages"
    #: 409: takeover, hand_back or send_message on a run whose server stopped (continue it instead).
    RUN_NOT_LIVE = "run_not_live"
    #: 400: ``project.set_model_key`` when the provider does not accept the key (nothing is saved).
    INVALID_MODEL_KEY = "invalid_model_key"
    #: 400: a call on the project's own model key was refused by the provider (a run ends with this error code); never falls back to Boxline's key.
    MODEL_KEY_REJECTED = "model_key_rejected"
    #: 502: another failure of a call on the project's own model key (the provider's text, key scrubbed).
    MODEL_ERROR = "model_error"
    #: 413: ``profiles.create(from_session=…)`` when the session's cookies and site storage exceed 16 MB.
    PROFILE_TOO_LARGE = "profile_too_large"
    #: 409: the session is not running (it stopped or was deleted): a call on it, an agent run in a deleted one.
    SESSION_NOT_RUNNING = "session_not_running"
    #: 409: ``sessions.resume`` when nothing was saved (a machine lost before its first checkpoint, a session from before stop and resume).
    NOTHING_SAVED = "nothing_saved"
    #: 409: ``sessions.resume`` on a deleted session.
    SESSION_NOT_STOPPED = "session_not_stopped"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"


# 5xx answers that are not the platform's own trouble, so they are not retried by themselves: configuration (a model
# provider, proxies or webhook delivery not set up), and a page that could not be loaded (fix the address; a page_timeout may be worth
# one more try of your own, with a longer timeout_ms).
_NOT_RETRYABLE = {"provider_unavailable", "proxy_unavailable", "webhooks_unavailable", "page_unreachable", "page_timeout", "not_a_web_page"}


class BoxlineError(Exception):
    """An error returned by the Boxline API (or no answer at all: see :class:`BoxlineConnectionError`)."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[httpx.Headers] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        #: The API's id for the request (error body, else the X-Request-Id header): quote it when asking for support.
        #: None when no response came back.
        self.request_id = request_id
        #: Your own id for the request, if you sent one (``options={"client_request_id": ...}``).
        self.client_request_id = client_request_id
        #: Response headers (RateLimit-*, Retry-After, …); None when no response came back.
        self.headers = headers
        #: The parsed error body, when there was one.
        self.body = body

    @property
    def details(self) -> Optional[Dict[str, Any]]:
        """Machine-readable facts the API sent with the error (``error.details``), or None. A 402 ``spend_limit`` or
        ``out_of_credit`` has ``details["limit"]``: ``"organization"``, ``"project"``, ``"plan"`` or ``"credit"`` (which
        limit refused the call)."""
        err = self.body.get("error") if isinstance(self.body, dict) else None
        details = err.get("details") if isinstance(err, dict) else None
        return details if isinstance(details, dict) else None

    @property
    def retryable(self) -> bool:
        """Whether trying the same call again later can succeed (rate limits, server errors, network trouble)."""
        if self.code == ErrorCode.IDEMPOTENCY_IN_PROGRESS:
            return True
        if self.code == ErrorCode.SEARCH_UNAVAILABLE:
            # Search not set up is for good; the provider limiting the platform says when to come back.
            return retry_after_seconds(self.headers) is not None
        if self.code in _NOT_RETRYABLE:
            return False
        return self.status == 429 or (self.status >= 500 and self.status != 501)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status}, code={self.code!r}, message={self.message!r}, request_id={self.request_id!r}, client_request_id={self.client_request_id!r})"


class RateLimitError(BoxlineError):
    """429: too many requests (``rate_limited``) or all of the plan's concurrent sessions in use (``concurrency_limit``)."""

    def __init__(self, status: int, code: str, message: str, **details: Any) -> None:
        super().__init__(status, code, message, **details)
        #: Seconds the server asked to wait (Retry-After), when it said.
        self.retry_after = retry_after_seconds(details.get("headers"))


class FeatureNotInPlanError(BoxlineError):
    """402 ``feature_not_in_plan``: the project's plan does not include this."""


class ProjectSuspendedError(BoxlineError):
    """403 ``project_suspended``: an admin suspended the project; the message says why."""


class IdempotencyMismatchError(BoxlineError):
    """422 ``idempotency_mismatch``: this Idempotency-Key was already used for a different request (another body or
    route), or, for a new API key, by another caller (another API key or console login)."""


class IdempotencyInProgressError(BoxlineError):
    """409 ``idempotency_in_progress``: the first request with this key is still being answered (retried automatically)."""


class InvalidCursorError(BoxlineError):
    """400 ``invalid_cursor``: ``after`` is not a ``next`` value of that list."""


class CaptchaTimeoutError(BoxlineError):
    """``captcha_timeout``: a CAPTCHA kept waiting for a person (a plain-English step, or wait_for_human)."""


class PageUnreachableError(BoxlineError):
    """502 ``page_unreachable`` (fetch, screenshot, pdf, extract): the page could not be loaded; the message says why."""


class PageTimeoutError(BoxlineError):
    """504 ``page_timeout`` (fetch, screenshot, pdf, extract): the page did not finish loading within ``timeout_ms``."""


class NotAWebPageError(BoxlineError):
    """422 ``not_a_web_page`` (fetch, extract, search, crawl): the address is a file Chrome only displays (a PDF), with no page text."""


class ModelRefusedError(BoxlineError):
    """422 ``model_refused`` (extract): the model declined to extract from these pages."""


class SearchUnavailableError(BoxlineError):
    """503 ``search_unavailable``: web search is not set up on this server, or its provider did not answer or is
    limiting the platform (then ``retryable``, with Retry-After)."""


class OutOfViewportError(BoxlineError):
    """400 ``out_of_viewport``: a mouse action's point is outside the page (or the computer-use screenshot)."""


class WebhookUrlNotAllowedError(BoxlineError):
    """400 ``webhook_url_not_allowed``: webhook endpoints must be public HTTPS addresses (the message says what is wrong)."""


class WebhooksUnavailableError(BoxlineError):
    """503 ``webhooks_unavailable``: webhook delivery is not set up on this server."""


class WebhookDisabledError(BoxlineError):
    """409 ``webhook_disabled``: the endpoint is switched off; turn it on (update enabled=True) first."""


class PayloadExpiredError(BoxlineError):
    """409 ``payload_expired``: the event's body is kept 7 days; that delivery can no longer be sent again."""


class VariablesWithExtensionsError(BoxlineError):
    """400 ``variables_with_extensions``: an agent run with variables in a session with extensions (see
    ``allow_with_extensions``)."""


class ExtensionDeniedError(BoxlineError):
    """403 ``extension_denied``: the operator does not allow this extension."""


class CrossSiteRequestError(BoxlineError):
    """403 ``cross_site_request``: a console-login upload that did not come from the platform's own sites."""


class InvalidExtensionError(BoxlineError):
    """400 ``invalid_extension``: the zip is not an acceptable Manifest V3 extension (the message says why)."""


class PayloadTooLargeError(BoxlineError):
    """413 ``payload_too_large``: the body is larger than the route allows (an extension zip: 10 MB)."""


class LimitReachedError(BoxlineError):
    """409 ``limit_reached``: the project has as many as it may (e.g. 100 extensions)."""


class MissingVariablesError(BoxlineError):
    """400 ``missing_variables``: a task run (or its schedule) has no value for a variable without a default; the
    message names them."""


class PlanLimitError(BoxlineError):
    """``plan_limit``: the plan allows no more of this (402: tasks, schedules switched on, searches on Free, project
    credentials beyond ``maxCredentials``, a session longer than the plan allows); the message says the limit."""


class TooManyCredentialValuesError(BoxlineError):
    """409 ``too_many_credential_values``: the session already hides as many earlier credential values in its output as
    it can (256 values or 256 KB); a session create, exec, agent run or step adding more is refused. Start a new
    session."""


class CredentialExistsError(BoxlineError):
    """409 ``credential_exists``: the project has a credential with that name; change it with ``credentials.update``."""


class CredentialNotAllowedError(BoxlineError):
    """400 ``credential_not_for_ai`` / ``credential_not_for_shell``: the credential's scope does not allow this use
    (scope "shell" is not for the AI; a credential goes into a shell only with scope "shell" or "all", or
    ``shell: True``)."""


class CredentialCodeTimeoutError(BoxlineError):
    """``credential_code_timeout``: a password with ``code_source`` "push" or "url" waited ``codeTimeoutSeconds`` and no
    fresh code or sign-in link came (push one with ``credentials.push_code``, or answer your ``code_url``). Raised by
    ``session.login`` and ``session.type_credential``; ``boxline-otp`` exits 1 with it."""


class CredentialLinkWrongSiteError(BoxlineError):
    """400 ``credential_link_wrong_site``: a sign-in link (pushed, or answered by ``code_url``) is not on one of the
    credential's sites, so it was not used (never opened)."""


class CredentialLoginFailedError(BoxlineError):
    """``credential_login_failed``: ``session.login`` could not sign in (the message says why: the run's last step).
    ``run_id`` is the short agent run that tried (``bx.agent.get(run_id)`` has its steps); None when the API did not
    say."""

    run_id: Optional[str] = None


class CredentialLoginTimeoutError(CredentialLoginFailedError):
    """``credential_login_timeout``: ``session.login`` took longer than its limit (15 steps, plus the credential's
    ``codeTimeoutSeconds`` when its codes come from your system), so the run was canceled. A kind of
    CredentialLoginFailedError: ``run_id`` is the run, and ``bx.agent.get(run_id)`` shows where it stood."""


class CodeUrlNotAllowedError(BoxlineError):
    """400 ``code_url_not_allowed``: a credential's ``code_url`` is not a public HTTPS address (the webhook address
    rules)."""


class MachineTooOldError(BoxlineError):
    """409 ``machine_too_old``: the session's machine comes from an image older than the API (during a deploy) and cannot
    take ``env`` or ``credentials``; start a new session."""


class NotContinuableError(BoxlineError):
    """409 ``not_continuable``: ``agent.continue_run`` on a run that did not stop at one of its limits, was already
    continued (the message names the run that did), or whose continue window has passed."""


class TooManyMessagesError(BoxlineError):
    """409 ``too_many_messages``: a run takes at most 50 messages (``agent.send_message``)."""


class RunNotLiveError(BoxlineError):
    """409 ``run_not_live``: ``agent.takeover``, ``hand_back`` or ``send_message`` on a run whose API server stopped (a
    restart, a deploy): no loop is left to act on it. Continue it (``agent.continue_run``) once it shows as failed with
    ``server_restarted``."""


class SessionNotRunningError(BoxlineError):
    """409 ``session_not_running``: the session is stopped or deleted (a call on a stopped session does not wake it: resume it first)."""


class NothingSavedError(BoxlineError):
    """409 ``nothing_saved``: ``sessions.resume`` on a stopped session that saved nothing (a machine lost before its first
    checkpoint, a session from before stop and resume)."""


class WebhookSignatureError(BoxlineError):
    """verify_webhook refused a delivery (not an API error: answer the sender 400). ``reason``: ``"malformed"`` (the
    Boxline-Signature header is missing or malformed), ``"timestamp_out_of_range"`` (too far from now: a replay) or
    ``"no_matching_signature"``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(400, ErrorCode.INVALID_SIGNATURE, message)
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return False


class AuthenticationError(BoxlineError):
    """401: missing or invalid API key."""


class NotFoundError(BoxlineError):
    """404: no such thing in this project."""


class BoxlineConnectionError(BoxlineError):
    """No response: the network failed or the server went away (retried automatically where safe)."""

    def __init__(self, message: str, code: str = ErrorCode.CONNECTION_ERROR, client_request_id: Optional[str] = None) -> None:
        super().__init__(0, code, message, client_request_id=client_request_id)

    @property
    def retryable(self) -> bool:
        return True


class BoxlineTimeoutError(BoxlineConnectionError):
    """The request took longer than its timeout."""

    def __init__(self, message: str, client_request_id: Optional[str] = None) -> None:
        super().__init__(message, ErrorCode.TIMEOUT, client_request_id)


_BY_CODE: Dict[str, Type[BoxlineError]] = {
    ErrorCode.RATE_LIMITED: RateLimitError,
    ErrorCode.CONCURRENCY_LIMIT: RateLimitError,
    ErrorCode.FEATURE_NOT_IN_PLAN: FeatureNotInPlanError,
    ErrorCode.PROJECT_SUSPENDED: ProjectSuspendedError,
    ErrorCode.IDEMPOTENCY_MISMATCH: IdempotencyMismatchError,
    ErrorCode.IDEMPOTENCY_IN_PROGRESS: IdempotencyInProgressError,
    ErrorCode.INVALID_CURSOR: InvalidCursorError,
    ErrorCode.CAPTCHA_TIMEOUT: CaptchaTimeoutError,
    ErrorCode.PAGE_UNREACHABLE: PageUnreachableError,
    ErrorCode.PAGE_TIMEOUT: PageTimeoutError,
    ErrorCode.NOT_A_WEB_PAGE: NotAWebPageError,
    ErrorCode.MODEL_REFUSED: ModelRefusedError,
    ErrorCode.SEARCH_UNAVAILABLE: SearchUnavailableError,
    ErrorCode.OUT_OF_VIEWPORT: OutOfViewportError,
    ErrorCode.WEBHOOK_URL_NOT_ALLOWED: WebhookUrlNotAllowedError,
    ErrorCode.WEBHOOKS_UNAVAILABLE: WebhooksUnavailableError,
    ErrorCode.WEBHOOK_DISABLED: WebhookDisabledError,
    ErrorCode.PAYLOAD_EXPIRED: PayloadExpiredError,
    ErrorCode.VARIABLES_WITH_EXTENSIONS: VariablesWithExtensionsError,
    ErrorCode.EXTENSION_DENIED: ExtensionDeniedError,
    ErrorCode.CROSS_SITE_REQUEST: CrossSiteRequestError,
    ErrorCode.INVALID_EXTENSION: InvalidExtensionError,
    ErrorCode.PAYLOAD_TOO_LARGE: PayloadTooLargeError,
    ErrorCode.LIMIT_REACHED: LimitReachedError,
    ErrorCode.MISSING_VARIABLES: MissingVariablesError,
    ErrorCode.PLAN_LIMIT: PlanLimitError,
    ErrorCode.TOO_MANY_CREDENTIAL_VALUES: TooManyCredentialValuesError,
    ErrorCode.CREDENTIAL_EXISTS: CredentialExistsError,
    ErrorCode.CREDENTIAL_NOT_FOR_AI: CredentialNotAllowedError,
    ErrorCode.CREDENTIAL_NOT_FOR_SHELL: CredentialNotAllowedError,
    ErrorCode.CREDENTIAL_CODE_TIMEOUT: CredentialCodeTimeoutError,
    ErrorCode.CREDENTIAL_LINK_WRONG_SITE: CredentialLinkWrongSiteError,
    ErrorCode.CREDENTIAL_LOGIN_FAILED: CredentialLoginFailedError,
    ErrorCode.CREDENTIAL_LOGIN_TIMEOUT: CredentialLoginTimeoutError,
    ErrorCode.CODE_URL_NOT_ALLOWED: CodeUrlNotAllowedError,
    ErrorCode.MACHINE_TOO_OLD: MachineTooOldError,
    ErrorCode.NOT_CONTINUABLE: NotContinuableError,
    ErrorCode.TOO_MANY_MESSAGES: TooManyMessagesError,
    ErrorCode.RUN_NOT_LIVE: RunNotLiveError,
    ErrorCode.SESSION_NOT_RUNNING: SessionNotRunningError,
    ErrorCode.NOTHING_SAVED: NothingSavedError,
}
_BY_STATUS: Dict[int, Type[BoxlineError]] = {401: AuthenticationError, 404: NotFoundError, 429: RateLimitError}


def make_error(status: int, code: str, message: str, **details: Any) -> BoxlineError:
    """The error class for an API error: by its code first, then by its status."""
    cls = _BY_CODE.get(code) or _BY_STATUS.get(status) or BoxlineError
    return cls(status, code, message, **details)


def retry_after_seconds(headers: Optional[httpx.Headers]) -> Optional[float]:
    """Retry-After in seconds (a number of seconds or an HTTP date); None when absent or unreadable."""
    value = headers.get("retry-after") if headers is not None else None
    if value is None or not value.strip():
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def error_from_response(res: httpx.Response) -> BoxlineError:
    """Turns an error response (body already read) into a typed error."""
    code = "http_error"
    message = f"{res.request.method} {res.request.url.path} failed with {res.status_code}"
    request_id = res.headers.get("x-request-id")
    body: Any = None
    try:
        body = res.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = err.get("code") if isinstance(err.get("code"), str) else code
            message = err.get("message") if isinstance(err.get("message"), str) else message
            request_id = err.get("requestId") if isinstance(err.get("requestId"), str) else request_id
    except ValueError:
        pass  # not JSON (a proxy's error page): keep the generic message
    client_request_id = res.headers.get("x-client-request-id") or res.request.headers.get("x-client-request-id")
    return make_error(res.status_code, code, message, request_id=request_id, client_request_id=client_request_id, headers=res.headers, body=body)
