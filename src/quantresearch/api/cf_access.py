"""Optional verification of the Cloudflare Access login token, as a second lock behind Access itself.

Cloudflare Access sits in front of the tunnel and adds a signed token (a JWT) to every request it lets through, in the
`Cf-Access-Jwt-Assertion` header. With this switched on, the API checks that token itself, so a request that somehow reaches
the API without passing Access (a misconfigured Access application, a hostname added to the tunnel later) is refused.

Off unless QR_VERIFY_CF_ACCESS is true, so local development is unchanged. When on, three settings are required and the server
refuses to start without them (it never silently runs unprotected):

    QR_VERIFY_CF_ACCESS=true
    QR_CF_ACCESS_AUD=<the Application Audience (AUD) tag of the API's Access application>
    QR_CF_ACCESS_TEAM=<your team name, from https://<team>.cloudflareaccess.com>

The team is configured rather than read from the token, because the signing keys are fetched from it and a token's own
claims cannot be trusted before it has been verified. Only RS256 is accepted. Keys are fetched at startup and refreshed every
24 hours, or when a token names a key that is not cached (Cloudflare rotates keys), at most once every five minutes.
Local access to http://127.0.0.1:8000 carries no token, so it is refused while this is on.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any, Callable

import jwt
from jwt.algorithms import RSAAlgorithm
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

HEADER = b"cf-access-jwt-assertion"
KEY_TTL_SECONDS = 24 * 3600
MIN_REFETCH_SECONDS = 300
_TEAM = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"", "0", "false", "no", "off"}


class AccessDenied(Exception):
    """The request carries no valid Access token. `reason` is for the log only and is never sent to the caller."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def fetch_jwks(url: str) -> dict:
    """Download Cloudflare's public signing keys (https only)."""
    if not url.startswith("https://"):
        raise ValueError("Access keys must be fetched over https")
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - scheme checked above
        return json.loads(response.read().decode("utf-8"))


def normalise_team(team: str) -> str:
    name = team.strip().lower().removeprefix("https://").rstrip("/").removesuffix(".cloudflareaccess.com")
    if not _TEAM.match(name):
        raise ValueError("QR_CF_ACCESS_TEAM must be your Access team name, for example 'myteam'")
    return name


class AccessVerifier:
    def __init__(self, team: str, audience: str, fetch: Callable[[str], dict] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if not audience.strip():
            raise ValueError("QR_CF_ACCESS_AUD is empty")
        self.team = normalise_team(team)
        self.audience = audience.strip()
        self.issuer = f"https://{self.team}.cloudflareaccess.com"
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs"
        self._fetch = fetch or fetch_jwks
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._loaded_at: float | None = None
        self._last_attempt: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ keys
    def refresh(self) -> bool:
        """Fetch the signing keys. On failure the previous keys stay in use. Returns whether it worked."""
        with self._lock:
            self._last_attempt = self._clock()
            try:
                document = self._fetch(self.certs_url)
                keys = {k["kid"]: RSAAlgorithm.from_jwk(json.dumps(k)) for k in document.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
            except Exception as exc:
                log.error("Could not fetch Cloudflare Access keys from %s: %s", self.certs_url, exc)
                return False
            if not keys:
                log.error("Cloudflare Access returned no usable signing keys from %s", self.certs_url)
                return False
            self._keys, self._loaded_at = keys, self._clock()
            log.info("Loaded %d Cloudflare Access signing key(s)", len(keys))
            return True

    def _can_retry(self) -> bool:
        return self._last_attempt is None or self._clock() - self._last_attempt >= MIN_REFETCH_SECONDS

    def _key_for(self, kid: str | None):
        if self._loaded_at is None or self._clock() - self._loaded_at >= KEY_TTL_SECONDS:
            if self._can_retry():
                self.refresh()  # A failed refresh keeps the old keys: they stay valid until Cloudflare rotates them.
        if kid not in self._keys and self._can_retry():
            self.refresh()  # A token signed by a newly rotated key.
        key = self._keys.get(kid) if kid else None
        if key is None:
            raise AccessDenied("no signing key matches the token" if self._keys else "no Access signing keys are loaded")
        return key

    # ------------------------------------------------------------ tokens
    def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AccessDenied("token is not a readable JWT") from None
        if header.get("alg") != "RS256":
            raise AccessDenied(f"unsupported algorithm {header.get('alg')!r}")
        key = self._key_for(header.get("kid"))
        try:
            return jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                              options={"require": ["exp", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            raise AccessDenied(f"{type(exc).__name__}") from None


def from_env(environ: dict[str, str] | None = None, fetch: Callable[[str], dict] | None = None) -> AccessVerifier | None:
    """None when verification is off. Raises ValueError when it is on but not fully configured."""
    env = os.environ if environ is None else environ
    flag = env.get("QR_VERIFY_CF_ACCESS", "").strip().lower()
    if flag in _FALSE:
        return None
    if flag not in _TRUE:
        raise ValueError(f"QR_VERIFY_CF_ACCESS={flag!r} is not understood; use true or false")
    audience, team = env.get("QR_CF_ACCESS_AUD", ""), env.get("QR_CF_ACCESS_TEAM", "")
    if not audience.strip() or not team.strip():
        raise ValueError("QR_VERIFY_CF_ACCESS is on, so QR_CF_ACCESS_AUD and QR_CF_ACCESS_TEAM must both be set; "
                         "refusing to start with the API unprotected")
    verifier = AccessVerifier(team, audience, fetch)
    if not verifier.refresh():
        log.warning("Access keys could not be loaded at startup. Every request is refused until they can be fetched.")
    return verifier


class CfAccessMiddleware:
    """Pure ASGI middleware, so it guards WebSockets as well as ordinary requests."""

    def __init__(self, app, verifier: AccessVerifier):
        self.app, self.verifier = app, verifier

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = dict(scope["headers"]).get(HEADER, b"").decode("latin-1").strip()
        try:
            if not token:
                raise AccessDenied("no Cf-Access-Jwt-Assertion header")
            await run_in_threadpool(self.verifier.verify, token)
        except AccessDenied as exc:
            log.warning("Refused %s %s: %s", scope["type"], scope.get("path", ""), exc.reason)
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Forbidden"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
