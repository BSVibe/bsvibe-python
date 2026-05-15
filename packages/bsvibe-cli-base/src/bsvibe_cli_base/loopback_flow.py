"""OAuth 2.0 Authorization Code grant with PKCE over a loopback redirect.

The BSVibe CLI's primary login path. Combines two specs:

* **RFC 7636 (PKCE)** — generate a random ``code_verifier``, send its
  ``S256`` hash as ``code_challenge`` on the authorize leg, prove
  possession by returning the verifier on the token-exchange leg.
* **RFC 8252 §7.3 (native-app loopback redirect)** — bind ``127.0.0.1:0``
  so the OS assigns a free ephemeral port, register that as the
  ``redirect_uri`` for this run, open the browser at
  ``/oauth/authorize``, then wait for the browser to redirect back to
  ``/callback?code=…&state=…`` on our listener.

Wire format:

1. ``GET /oauth/authorize?response_type=code&client_id=…&redirect_uri=…
   &code_challenge=…&code_challenge_method=S256&state=…&scope=…&audience=…``
   → 302 to ``<redirect_uri>?code=…&state=…`` once the user approves.
2. ``POST /oauth/token`` (form-encoded) with ``grant_type=authorization_code``,
   the captured ``code``, the matching ``code_verifier``, the same
   ``redirect_uri``, and ``client_id`` → ``{access_token, refresh_token, …}``.

The ``state`` parameter is a CSRF defense — the listener rejects callbacks
whose state doesn't match what we sent. The verifier is held only in the
calling process's memory; it never touches keyring or disk.

Built on :class:`bsvibe_core.http.HttpClientBase` so retry, structured
logging, and credential redaction are inherited.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from bsvibe_core.http import HttpClientBase

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Errors + grant model
# ---------------------------------------------------------------------------


class LoopbackFlowError(Exception):
    """Raised on any non-recoverable failure in the loopback flow."""


class LoopbackFlowTimeoutError(LoopbackFlowError):
    """The loopback listener did not receive a callback in time."""


class LoopbackFlowStateMismatchError(LoopbackFlowError):
    """The callback's ``state`` did not match the value we sent.

    Either the user opened a stale authorize URL or a CSRF attempt
    targeted the loopback listener. Both are terminal — the caller must
    re-run ``login``.
    """


@dataclass(frozen=True)
class TokenGrant:
    """Token pair returned by ``/oauth/token``.

    Carries both the initial authorization-code grant AND subsequent
    refresh-token rotations (see :mod:`bsvibe_cli_base.http`), so the
    name is intentionally provider-agnostic.
    """

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "Bearer"


# ---------------------------------------------------------------------------
# PKCE — RFC 7636 §4
# ---------------------------------------------------------------------------


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` per RFC 7636 §4.

    ``code_verifier`` is 64 url-safe random bytes which yields an 86-char
    base64url string — comfortably inside the spec's 43-128 envelope and
    well above the ~256-bit entropy floor recommended for native apps.
    ``code_challenge`` is the ``S256`` digest, base64url-encoded with
    padding stripped (RFC 7636 §4.2).
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


def build_authorize_url(
    auth_url: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str | None = None,
    audience: str | None = None,
    authorize_path: str = "/oauth/authorize",
) -> str:
    """Construct the URL the user is sent to in their browser."""
    base = auth_url.rstrip("/") + authorize_path
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if scope:
        params["scope"] = scope
    if audience:
        params["audience"] = audience
    return f"{base}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Loopback listener — single-shot HTTP server on 127.0.0.1:<ephemeral>
# ---------------------------------------------------------------------------


@dataclass
class _CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


_SUCCESS_HTML = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>BSVibe \xe2\x80\x94 Login complete</title></head>
<body style="font-family:system-ui,-apple-system,sans-serif;text-align:center;padding:48px;color:#222">
<h1 style="margin-bottom:8px">Login complete</h1>
<p>You can close this tab and return to your terminal.</p>
</body></html>
"""

_ERROR_HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>BSVibe — Login failed</title></head>
<body style="font-family:system-ui,-apple-system,sans-serif;text-align:center;padding:48px;color:#222">
<h1 style="margin-bottom:8px">Login failed</h1>
<p>Auth server returned: <code>{error}</code></p>
<p>Return to your terminal for details.</p>
</body></html>
"""


_STATUS_TEXT: dict[int, str] = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
}


def _write_response(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    text = _STATUS_TEXT.get(status, "Unknown")
    head = (
        f"HTTP/1.1 {status} {text}\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii")
    writer.write(head + body)


@dataclass
class LoopbackListener:
    """One-shot HTTP listener that resolves on the OAuth redirect.

    Instances are created by :func:`bind_loopback_listener` and consumed
    by :meth:`wait_for_callback`. Always pair with :meth:`close` (the
    public API does this for you via ``run_login_flow``).
    """

    host: str
    port: int
    _server: asyncio.AbstractServer
    _future: asyncio.Future[_CallbackResult]

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.host}:{self.port}/callback"

    async def wait_for_callback(self, expected_state: str, timeout_s: float) -> str:
        """Block until the listener observes a single OAuth redirect.

        Returns the ``code`` query param on success. Raises
        :class:`LoopbackFlowTimeoutError` if no callback arrives,
        :class:`LoopbackFlowStateMismatchError` on CSRF state mismatch,
        :class:`LoopbackFlowError` on an ``?error=`` redirect or any
        malformed callback.
        """
        try:
            result = await asyncio.wait_for(asyncio.shield(self._future), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise LoopbackFlowTimeoutError(f"loopback callback not received within {timeout_s}s") from exc

        if result.error:
            detail = f" — {result.error_description}" if result.error_description else ""
            raise LoopbackFlowError(f"auth server returned error: {result.error}{detail}")
        if result.state != expected_state:
            raise LoopbackFlowStateMismatchError(f"state mismatch: expected {expected_state!r}, got {result.state!r}")
        if not result.code:
            raise LoopbackFlowError("auth server redirect missing both code and error")
        return result.code

    async def close(self) -> None:
        if not self._future.done():
            self._future.cancel()
        if self._server.is_serving():
            self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:  # pragma: no cover - server already torn down
            pass


async def bind_loopback_listener() -> LoopbackListener:
    """Bind ``127.0.0.1:0`` and return a listener waiting for ``/callback``.

    The kernel assigns a free ephemeral port. RFC 8252 §7.3 mandates
    this pattern for native apps so a stolen redirect URI cannot fix a
    well-known port that a malicious local app could squat on.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_CallbackResult] = loop.create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await reader.readline()).decode("ascii", errors="replace").strip()
            # Drain headers (we don't read the body — GET request).
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break

            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                _write_response(writer, 400, b"<h1>Bad Request</h1>")
                return
            method, target = parts[0], parts[1]
            if method != "GET":
                _write_response(writer, 405, b"<h1>Method Not Allowed</h1>")
                return
            parsed = urllib.parse.urlsplit(target)
            if parsed.path != "/callback":
                _write_response(writer, 404, b"<h1>Not Found</h1>")
                return

            qs = urllib.parse.parse_qs(parsed.query)
            result = _CallbackResult(
                code=(qs.get("code") or [None])[0],
                state=(qs.get("state") or [None])[0],
                error=(qs.get("error") or [None])[0],
                error_description=(qs.get("error_description") or [None])[0],
            )
            if not future.done():
                future.set_result(result)

            if result.error:
                _write_response(
                    writer,
                    400,
                    _ERROR_HTML_TEMPLATE.format(error=result.error).encode("utf-8"),
                )
            else:
                _write_response(writer, 200, _SUCCESS_HTML)
        except Exception as exc:  # pragma: no cover - rare wire-level glitches
            logger.warning("loopback_callback_handler_error", error=str(exc))
            if not future.done():
                future.set_exception(exc)
        finally:
            try:
                await writer.drain()
            except Exception:  # pragma: no cover
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    sockname = server.sockets[0].getsockname()
    return LoopbackListener(
        host=sockname[0],
        port=sockname[1],
        _server=server,
        _future=future,
    )


# ---------------------------------------------------------------------------
# Token-endpoint client
# ---------------------------------------------------------------------------


class LoopbackFlowClient(HttpClientBase):
    """Client for ``/oauth/token`` ``authorization_code`` exchanges.

    Also exposes :meth:`run_login_flow` which wires the listener +
    browser + exchange into one call. Subcommands that only need
    ``refresh_token`` rotation continue to use
    :class:`bsvibe_cli_base.http.CliHttpClient` — the refresh path is
    independent of how the initial grant was obtained.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        retries: int = 1,
        token_path: str = "/oauth/token",
        authorize_path: str = "/oauth/authorize",
    ) -> None:
        super().__init__(base_url, http=http, timeout_s=timeout_s, retries=retries)
        self._client_id = client_id
        self._token_path = token_path
        self._authorize_path = authorize_path

    @property
    def client_id(self) -> str:
        return self._client_id

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> TokenGrant:
        """Exchange an authorization ``code`` + PKCE verifier for tokens."""
        body: dict[str, Any] = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
        resp = await self.post(self._token_path, data=body)
        if resp.status_code >= 400:
            raise LoopbackFlowError(f"token exchange failed: {resp.status_code} {_error_msg(resp)}")
        data = resp.json()
        access = data.get("access_token")
        if not access:
            raise LoopbackFlowError(f"token endpoint returned 2xx without access_token: {data!r}")
        return TokenGrant(
            access_token=access,
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
        )

    async def run_login_flow(
        self,
        *,
        scope: str | None,
        audience: str | None,
        open_browser: Callable[[str], Any] = webbrowser.open,
        announce: Callable[[str], None] | None = None,
        callback_timeout_s: float = 120.0,
    ) -> TokenGrant:
        """Drive the full PKCE-on-loopback flow.

        ``open_browser`` defaults to :func:`webbrowser.open` so the
        terminal session pops a tab automatically. Tests override it to
        fire a synthetic callback directly against the listener.
        ``announce`` (when supplied) is invoked with the authorize URL so
        a ``--no-browser`` CLI mode can print it for the operator to
        paste manually.
        """
        verifier, challenge = generate_pkce()
        state = secrets.token_urlsafe(16)
        listener = await bind_loopback_listener()
        try:
            authorize_url = build_authorize_url(
                self._base_url,
                client_id=self._client_id,
                redirect_uri=listener.redirect_uri,
                code_challenge=challenge,
                state=state,
                scope=scope,
                audience=audience,
                authorize_path=self._authorize_path,
            )
            if announce is not None:
                announce(authorize_url)
            try:
                open_browser(authorize_url)
            except Exception as exc:
                # Browser launch failed but the user may still paste the URL
                # if ``announce`` already showed it. Log and continue waiting.
                logger.warning("loopback_browser_open_failed", error=str(exc))
            code = await listener.wait_for_callback(state, callback_timeout_s)
            return await self.exchange_code(
                code=code,
                code_verifier=verifier,
                redirect_uri=listener.redirect_uri,
            )
        finally:
            await listener.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_msg(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)
    return str(data)


__all__ = [
    "TokenGrant",
    "LoopbackFlowClient",
    "LoopbackFlowError",
    "LoopbackFlowTimeoutError",
    "LoopbackFlowStateMismatchError",
    "LoopbackListener",
    "bind_loopback_listener",
    "build_authorize_url",
    "generate_pkce",
]
