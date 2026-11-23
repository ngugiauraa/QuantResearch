import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from quantresearch.api import cf_access
from quantresearch.api.cf_access import AccessDenied, AccessVerifier, CfAccessMiddleware
from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths

TEAM, AUD = "myteam", "aud-tag-1234"
ISSUER = f"https://{TEAM}.cloudflareaccess.com"
HEADER = "Cf-Access-Jwt-Assertion"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def key():
    return make_key()


@pytest.fixture(scope="module")
def other_key():
    return make_key()


def jwk(private, kid):
    return {**RSAAlgorithm.to_jwk(private.public_key(), as_dict=True), "kid": kid, "alg": "RS256", "use": "sig"}


def token(private, kid="k1", **claims):
    body = {"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600, "iat": int(time.time()), "email": "me@example.com"}
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, private, algorithm="RS256", headers={"kid": kid})


class Keys:
    """A stand-in for Cloudflare's certs endpoint that counts how often it is called."""

    def __init__(self, *entries):
        self.entries, self.calls, self.fail = list(entries), 0, False

    def __call__(self, url):
        self.calls += 1
        assert url == f"{ISSUER}/cdn-cgi/access/certs"
        if self.fail:
            raise OSError("network down")
        return {"keys": self.entries, "public_cert": {"kid": "ignored"}}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def verifier(keys, clock=None):
    v = AccessVerifier(TEAM, AUD, fetch=keys, clock=clock or Clock())
    assert v.refresh()
    return v


# ---------------------------------------------------------------- the token checks
def test_a_valid_token_is_accepted_and_its_claims_returned(key):
    claims = verifier(Keys(jwk(key, "k1"))).verify(token(key))
    assert claims["email"] == "me@example.com" and claims["iss"] == ISSUER


@pytest.mark.parametrize("name,build", [
    ("expired", lambda k: token(k, exp=int(time.time()) - 10)),
    ("no expiry", lambda k: token(k, exp=None)),
    ("wrong audience", lambda k: token(k, aud=["some-other-app"])),
    ("no audience", lambda k: token(k, aud=None)),
    ("wrong issuer", lambda k: token(k, iss="https://attacker.cloudflareaccess.com")),
    ("garbage", lambda k: "not.a.jwt"),
    ("empty", lambda k: ""),
])
def test_bad_tokens_are_refused(key, name, build):
    with pytest.raises(AccessDenied):
        verifier(Keys(jwk(key, "k1"))).verify(build(key))


def test_a_token_signed_by_someone_elses_key_is_refused(key, other_key):
    with pytest.raises(AccessDenied):
        verifier(Keys(jwk(key, "k1"))).verify(token(other_key, kid="k1"))


def test_only_rs256_is_accepted_so_none_and_hmac_tricks_fail(key):
    v = verifier(Keys(jwk(key, "k1")))
    unsigned = jwt.encode({"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600}, None, algorithm="none", headers={"kid": "k1"})
    with pytest.raises(AccessDenied, match="algorithm"):
        v.verify(unsigned)
    # The classic confusion attack: sign with HMAC using the public key's bytes as the secret.
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    segments = [base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b"=") for x in (
        {"alg": "HS256", "typ": "JWT", "kid": "k1"}, {"aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 600})]
    import hashlib
    import hmac
    signing_input = b".".join(segments)
    signature = base64.urlsafe_b64encode(hmac.new(public_pem, signing_input, hashlib.sha256).digest()).rstrip(b"=")
    with pytest.raises(AccessDenied, match="algorithm"):
        v.verify((signing_input + b"." + signature).decode())


# ---------------------------------------------------------------- key caching and rotation
def test_keys_are_fetched_once_and_cached_for_24_hours():
    keys, clock = Keys(jwk(make_key(), "k1")), Clock()
    v = verifier(keys, clock)
    assert keys.calls == 1
    replacement = make_key()
    keys.entries = [jwk(replacement, "k1")]  # Cloudflare now publishes a different key under the same id.
    clock.now += 23 * 3600
    with pytest.raises(AccessDenied):
        v.verify(token(replacement, "k1"))  # Still inside the 24 hours: the cached key is used, with no refetch.
    assert keys.calls == 1
    clock.now += 2 * 3600  # Past 24 hours: the next check refreshes.
    assert v.verify(token(replacement, "k1"))
    assert keys.calls == 2


def test_a_rotated_key_is_picked_up_but_unknown_keys_cannot_hammer_the_endpoint(key, other_key):
    keys, clock = Keys(jwk(key, "old")), Clock()
    v = verifier(keys, clock)
    keys.entries = [jwk(key, "old"), jwk(other_key, "new")]
    clock.now += 600
    assert v.verify(token(other_key, "new")) and keys.calls == 2  # Unknown kid, more than 5 minutes since the last fetch.
    for _ in range(5):
        with pytest.raises(AccessDenied):
            v.verify(token(other_key, "never-published"))
    assert keys.calls == 2  # Within 5 minutes of that fetch, so no further requests however many bad tokens arrive.
    clock.now += 301
    with pytest.raises(AccessDenied):
        v.verify(token(other_key, "never-published"))
    assert keys.calls == 3  # One more attempt once the floor has passed.


def test_if_the_fetch_fails_the_old_keys_keep_working(key):
    keys, clock = Keys(jwk(key, "k1")), Clock()
    v = verifier(keys, clock)
    keys.fail = True
    clock.now += 25 * 3600
    assert v.verify(token(key, "k1"))  # Stale but still cryptographically valid.
    assert keys.calls == 2


def test_with_no_keys_ever_loaded_everything_is_refused(key):
    keys = Keys(jwk(key, "k1"))
    keys.fail = True
    v = AccessVerifier(TEAM, AUD, fetch=keys, clock=Clock())
    assert v.refresh() is False
    with pytest.raises(AccessDenied, match="no Access signing keys"):
        v.verify(token(key))


# ---------------------------------------------------------------- configuration
def test_off_by_default_and_a_clear_off_switch():
    assert cf_access.from_env({}) is None
    for value in ("", "false", "0", "no", "off", "FALSE"):
        assert cf_access.from_env({"QR_VERIFY_CF_ACCESS": value}) is None


@pytest.mark.parametrize("env", [
    {"QR_VERIFY_CF_ACCESS": "true"},
    {"QR_VERIFY_CF_ACCESS": "true", "QR_CF_ACCESS_AUD": AUD},
    {"QR_VERIFY_CF_ACCESS": "true", "QR_CF_ACCESS_TEAM": TEAM},
    {"QR_VERIFY_CF_ACCESS": "true", "QR_CF_ACCESS_AUD": "  ", "QR_CF_ACCESS_TEAM": TEAM},
])
def test_turning_it_on_without_full_settings_refuses_to_start(env):
    with pytest.raises(ValueError, match="refusing to start"):
        cf_access.from_env(env, fetch=Keys())


def test_a_typo_in_the_switch_does_not_silently_disable_it():
    with pytest.raises(ValueError, match="not understood"):
        cf_access.from_env({"QR_VERIFY_CF_ACCESS": "flase"})


@pytest.mark.parametrize("team", ["myteam", "MyTeam", "myteam.cloudflareaccess.com", "https://myteam.cloudflareaccess.com/"])
def test_the_team_name_is_normalised(team):
    assert cf_access.normalise_team(team) == "myteam"


@pytest.mark.parametrize("team", ["evil.example.com/x", "a b", "", "my_team", "myteam@evil.com"])
def test_a_team_that_could_redirect_the_key_fetch_is_refused(team):
    with pytest.raises(ValueError):
        cf_access.normalise_team(team)


# ---------------------------------------------------------------- the middleware, on the real app
@pytest.fixture()
def guarded(tmp_path, monkeypatch, key):
    keys = Keys(jwk(key, "k1"))
    monkeypatch.setattr(cf_access, "fetch_jwks", keys)
    monkeypatch.setenv("QR_VERIFY_CF_ACCESS", "true")
    monkeypatch.setenv("QR_CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("QR_CF_ACCESS_TEAM", TEAM)
    monkeypatch.setenv("QR_CORS_ORIGINS", "https://dashboard.example.com")
    return TestClient(create_app(Services(Paths(tmp_path)))), keys


def test_with_verification_on_a_valid_token_passes(guarded, key):
    client, keys = guarded
    assert keys.calls == 1  # Fetched at startup.
    assert client.get("/api/health", headers={HEADER: token(key)}).status_code == 200


def test_with_verification_on_a_missing_token_is_403(guarded):
    client, _ = guarded
    response = client.get("/api/health")
    assert response.status_code == 403 and response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("bad", ["garbage", "a.b.c"])
def test_with_verification_on_an_invalid_token_is_403(guarded, bad):
    client, _ = guarded
    assert client.get("/api/health", headers={HEADER: bad}).status_code == 403


def test_with_verification_on_an_expired_or_foreign_token_is_403(guarded, key, other_key):
    client, _ = guarded
    assert client.get("/api/health", headers={HEADER: token(key, exp=int(time.time()) - 5)}).status_code == 403
    assert client.get("/api/health", headers={HEADER: token(other_key)}).status_code == 403


def test_every_route_is_guarded_not_just_health(guarded):
    client, _ = guarded
    for path in ("/api/overview", "/api/bars?tf=h1", "/", "/does-not-exist"):
        assert client.get(path).status_code == 403, path


def test_a_cors_preflight_still_gets_its_answer_without_a_token(guarded):
    client, _ = guarded
    response = client.options("/api/health", headers={"Origin": "https://dashboard.example.com", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://dashboard.example.com"


def test_the_403_is_readable_by_the_dashboard_origin(guarded):
    client, _ = guarded
    response = client.get("/api/health", headers={"Origin": "https://dashboard.example.com"})
    assert response.status_code == 403 and response.headers["access-control-allow-origin"] == "https://dashboard.example.com"


def test_with_verification_off_everything_passes_without_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("QR_VERIFY_CF_ACCESS", raising=False)
    client = TestClient(create_app(Services(Paths(tmp_path))))
    assert client.get("/api/health").status_code == 200


def test_the_app_refuses_to_start_when_on_but_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("QR_VERIFY_CF_ACCESS", "true")
    monkeypatch.delenv("QR_CF_ACCESS_AUD", raising=False)
    monkeypatch.delenv("QR_CF_ACCESS_TEAM", raising=False)
    with pytest.raises(ValueError, match="refusing to start"):
        create_app(Services(Paths(tmp_path)))


# ---------------------------------------------------------------- websockets
def test_websockets_are_guarded_too(key):
    async def hello(request):
        return PlainTextResponse("hi")

    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app = Starlette(routes=[Route("/", hello), WebSocketRoute("/ws", ws)])
    app.add_middleware(CfAccessMiddleware, verifier=verifier(Keys(jwk(key, "k1"))))
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws"):
            pass
    assert refused.value.code == 1008
    with client.websocket_connect("/ws", headers={HEADER: token(key)}) as socket:
        assert socket.receive_text() == "hello"
    assert client.get("/").status_code == 403 and client.get("/", headers={HEADER: token(key)}).text == "hi"
