"""OAuth 2.1 + PKCE helpers with session-only token storage by default."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from .config import Config
from .errors import AuthenticationRequired, ConfigurationError, OAuthError, ProviderError, ValidationError
from .http import HttpTransport, form_encode
from .security import EndpointAllowlist, constant_time_equal, random_opaque_id


@dataclass(frozen=True, slots=True)
class PKCEPair:
    verifier: str
    challenge: str


def create_pkce() -> PKCEPair:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return PKCEPair(verifier=verifier, challenge=challenge)


@dataclass(frozen=True, slots=True)
class OAuthToken:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0
    scope: str | None = None
    token_type: str = "OAuth"

    def needs_refresh(self, now: float, skew_seconds: int = 60) -> bool:
        return self.expires_at <= now + skew_seconds

    @classmethod
    def from_payload(cls, payload: object, *, now: float) -> "OAuthToken":
        if not isinstance(payload, Mapping):
            raise OAuthError("token endpoint returned an invalid response")
        access = payload.get("access_token")
        if not isinstance(access, str) or not access or any(ch.isspace() for ch in access):
            raise OAuthError("token endpoint returned an invalid access token")
        refresh = payload.get("refresh_token")
        if refresh is not None and (not isinstance(refresh, str) or not refresh or any(ch.isspace() for ch in refresh)):
            raise OAuthError("token endpoint returned an invalid refresh token")
        expires = payload.get("expires_in", 3600)
        try:
            expires_float = float(expires)
        except (TypeError, ValueError) as exc:
            raise OAuthError("token endpoint returned an invalid expiry") from exc
        if not 0 < expires_float <= 31_536_000:
            raise OAuthError("token endpoint returned an invalid expiry")
        scope = payload.get("scope") if isinstance(payload.get("scope"), str) else None
        return cls(
            access_token=access,
            refresh_token=refresh.strip() if isinstance(refresh, str) else None,
            expires_at=now + expires_float,
            scope=scope,
            token_type=str(payload.get("token_type") or "OAuth")[:40],
        )

    @classmethod
    def from_storage(cls, payload: object) -> "OAuthToken | None":
        # Kept for test fixtures and future safe stores; the default service does
        # not read a token file.
        if not isinstance(payload, Mapping) or not isinstance(payload.get("access_token"), str):
            return None
        try:
            return cls(
                access_token=payload["access_token"],
                refresh_token=payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else None,
                expires_at=float(payload.get("expires_at", 0)),
                scope=payload.get("scope") if isinstance(payload.get("scope"), str) else None,
                token_type=str(payload.get("token_type") or "OAuth")[:40],
            )
        except (TypeError, ValueError):
            return None


TokenSet = OAuthToken


class TokenStore:
    """Storage interface. Production default is SessionTokenStore."""

    def load(self) -> OAuthToken | None:
        raise NotImplementedError

    def save(self, token: OAuthToken) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    @property
    def storage_kind(self) -> str:
        return "session-only"


class SessionTokenStore(TokenStore):
    def __init__(self) -> None:
        self._token: OAuthToken | None = None
        self._lock = threading.RLock()

    def load(self) -> OAuthToken | None:
        with self._lock:
            return self._token

    def save(self, token: OAuthToken) -> None:
        with self._lock:
            self._token = token

    def clear(self) -> None:
        with self._lock:
            self._token = None

    @property
    def storage_kind(self) -> str:
        return "session-only"


class OAuthStateManager:
    """One-time CSRF state registry bound to a redirect URI."""

    def __init__(self, ttl_seconds: int = 600, clock: Callable[[], float] | None = None) -> None:
        if not 60 <= ttl_seconds <= 600:
            raise ValidationError("OAuth state TTL must be between 60 and 600 seconds")
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time
        self._flows: dict[str, OAuthFlow] = {}
        self._lock = threading.RLock()

    def _purge(self) -> None:
        now = self.clock()
        for state, flow in list(self._flows.items()):
            if flow.created_at + self.ttl_seconds <= now:
                self._flows.pop(state, None)

    def create(self, *, client_id: str, redirect_uri: str, now: float | None = None) -> "OAuthFlow":
        pair = create_pkce()
        created_at = self.clock() if now is None else now
        flow = OAuthFlow(
            state=random_opaque_id(32),
            code_verifier=pair.verifier,
            code_challenge=pair.challenge,
            redirect_uri=redirect_uri,
            client_id=client_id,
            created_at=created_at,
        )
        with self._lock:
            self._purge()
            self._flows[flow.state] = flow
        return flow

    def consume(self, state: str, *, redirect_uri: str, now: float | None = None) -> "OAuthFlow":
        if not isinstance(state, str) or not state or len(state) > 512:
            raise OAuthError("OAuth state is invalid or expired")
        with self._lock:
            self._purge()
            flow = self._flows.pop(state, None)
        if flow is None:
            raise OAuthError("OAuth state is invalid or already used")
        current = self.clock() if now is None else now
        if current - flow.created_at >= self.ttl_seconds:
            raise OAuthError("OAuth state is invalid or expired")
        if not constant_time_equal(flow.redirect_uri, redirect_uri):
            raise OAuthError("OAuth callback URI does not match the browser flow")
        return flow

    def clear(self) -> None:
        with self._lock:
            self._flows.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._flows)


@dataclass(frozen=True, slots=True)
class OAuthFlow:
    state: str
    code_verifier: str = field(repr=False)
    code_challenge: str
    redirect_uri: str
    client_id: str
    created_at: float


class OAuthClient:
    def __init__(
        self,
        config: Config,
        transport: HttpTransport,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.clock = clock or time.time
        self.allowlist = EndpointAllowlist(config.api_base_url, config.authorize_url, config.token_url)

    def authorization_url(self, flow: OAuthFlow) -> str:
        if not self.config.client_id:
            raise ConfigurationError("SOUNDCLOUD_CLIENT_ID is required for OAuth")
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": flow.redirect_uri,
            "response_type": "code",
            "code_challenge": flow.code_challenge,
            "code_challenge_method": "S256",
            "state": flow.state,
        }
        # Never invent or default a scope; only pass an explicit provider value.
        if self.config.scope:
            params["scope"] = self.config.scope
        return self.allowlist.assert_authorize_url(self.config.authorize_url + "?" + urlencode(params))

    def _post_token(self, values: Mapping[str, str], *, basic: bool = False) -> OAuthToken:
        if not self.config.client_id or not self.config.client_secret:
            raise ConfigurationError("SoundCloud client credentials are not configured")
        form = {"client_id": self.config.client_id, "client_secret": self.config.client_secret, **values}
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        # SoundCloud's client-credentials flow requires HTTP Basic. Do not put
        # secrets in URLs, logs, or MCP responses.
        if basic:
            import base64 as _base64

            raw = f"{self.config.client_id}:{self.config.client_secret}".encode("utf-8")
            headers["Authorization"] = "Basic " + _base64.b64encode(raw).decode("ascii")
            form.pop("client_id", None)
            form.pop("client_secret", None)
        response = self.transport.request(
            "POST",
            self.allowlist.assert_token_url(self.config.token_url),
            headers=headers,
            body=form_encode(form),
            timeout=self.config.request_timeout_seconds,
        )
        if response.status < 200 or response.status >= 300:
            raise OAuthError(f"token exchange failed with HTTP {response.status}")
        return OAuthToken.from_payload(response.json(), now=self.clock())

    def exchange_code(self, flow: OAuthFlow, code: str) -> OAuthToken:
        if not isinstance(code, str) or not code or len(code) > 4096 or any(ch.isspace() for ch in code):
            raise ValidationError("OAuth authorization code is invalid")
        return self._post_token(
            {
                "grant_type": "authorization_code",
                "redirect_uri": flow.redirect_uri,
                "code_verifier": flow.code_verifier,
                "code": code,
            }
        )

    def refresh(self, refresh_token: str) -> OAuthToken:
        if not refresh_token:
            raise AuthenticationRequired("refresh token is unavailable")
        return self._post_token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def client_credentials(self) -> OAuthToken:
        return self._post_token({"grant_type": "client_credentials"}, basic=True)


class TokenManager:
    """Serialized refresh rotation; uncertain refresh invalidates old state."""

    def __init__(
        self,
        client: OAuthClient,
        store: TokenStore,
        clock: Callable[[], float] | None = None,
        refresh_skew_seconds: int = 60,
    ) -> None:
        self.client = client
        self.store = store
        self.clock = clock or time.time
        self.refresh_skew_seconds = refresh_skew_seconds
        self._lock = threading.RLock()

    def load(self) -> OAuthToken | None:
        return self.store.load()

    def save(self, token: OAuthToken) -> None:
        with self._lock:
            self.store.save(token)

    def clear(self) -> None:
        with self._lock:
            self.store.clear()

    def get_access_token(self) -> str:
        with self._lock:
            token = self.store.load()
            if token is None:
                raise AuthenticationRequired("SoundCloud account is not connected")
            if not token.needs_refresh(self.clock(), self.refresh_skew_seconds):
                return token.access_token
            old_refresh = token.refresh_token
            if not old_refresh:
                self.store.clear()
                raise AuthenticationRequired("expired access token requires reconnect")
            try:
                rotated = self.client.refresh(old_refresh)
                # A missing refresh token is not safe to replay: SoundCloud
                # refresh tokens are single-use, so invalidate old state.
                if not rotated.refresh_token:
                    self.store.clear()
                    raise AuthenticationRequired("refresh rotation did not return a refresh token")
                self.store.save(rotated)
                return rotated.access_token
            except Exception as exc:
                self.store.clear()
                if isinstance(exc, AuthenticationRequired):
                    raise
                raise AuthenticationRequired("token refresh failed; reconnect is required") from exc

    @property
    def connected(self) -> bool:
        return self.store.load() is not None

    @property
    def storage_kind(self) -> str:
        return self.store.storage_kind


@dataclass(frozen=True, slots=True)
class BrowserFlow:
    flow_id: str
    state: str
    capability: str = field(repr=False)
    created_at: float
    expires_at: float
    status: str = "authorization_required"
    error_code: str | None = None


class OAuthManager:
    """Loopback browser-flow registry around the one-time state manager."""

    def __init__(
        self,
        config: Config,
        client: OAuthClient,
        token_manager: TokenManager,
        verify_token: Callable[[OAuthToken], bool] | None = None,
        on_connected: Callable[[], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.token_manager = token_manager
        self.verify_token = verify_token
        self.on_connected = on_connected
        self.clock = clock or time.time
        self.states = OAuthStateManager(config.oauth_state_ttl_seconds, self.clock)
        self._flows: dict[str, tuple[BrowserFlow, OAuthFlow]] = {}
        self._completed: dict[str, tuple[BrowserFlow, OAuthFlow]] = {}
        self._generation = 0
        self._lock = threading.RLock()

    def _purge(self) -> None:
        now = self.clock()
        for registry in (self._flows, self._completed):
            for flow_id, (browser, _) in list(registry.items()):
                if browser.expires_at <= now:
                    registry.pop(flow_id, None)

    def start(self) -> BrowserFlow:
        if not self.config.client_id or not self.config.client_secret:
            raise ConfigurationError("SoundCloud client credentials are not configured")
        now = self.clock()
        oauth_flow = self.states.create(
            client_id=self.config.client_id, redirect_uri=self.config.redirect_uri, now=now
        )
        browser = BrowserFlow(
            flow_id=random_opaque_id(24),
            state=oauth_flow.state,
            capability=random_opaque_id(32),
            created_at=now,
            expires_at=now + self.config.oauth_state_ttl_seconds,
        )
        with self._lock:
            self._purge()
            self._flows[browser.flow_id] = (browser, oauth_flow)
        return browser

    def authorization_url(self, flow_id: str, capability: str) -> str:
        browser, flow = self._get(flow_id, capability)
        if browser.status != "authorization_required":
            raise OAuthError("authorization flow is no longer active")
        return self.client.authorization_url(flow)

    def _get(self, flow_id: str, capability: str) -> tuple[BrowserFlow, OAuthFlow]:
        with self._lock:
            self._purge()
            pair = self._flows.get(flow_id) or self._completed.get(flow_id)
        if pair is None or not capability or not constant_time_equal(capability, pair[0].capability):
            raise OAuthError("authorization viewer capability is invalid or expired")
        return pair

    def complete_callback(self, *, state: str, code: str | None = None, error: str | None = None) -> BrowserFlow:
        # Consume state before any provider request. Replays cannot retry token exchange.
        oauth_flow = self.states.consume(state, redirect_uri=self.config.redirect_uri, now=self.clock())
        with self._lock:
            self._purge()
            match = next(((flow_id, pair) for flow_id, pair in self._flows.items() if pair[1].state == oauth_flow.state), None)
            if match is None:
                raise OAuthError("OAuth browser flow is expired or unknown")
            flow_id, (browser, _) = match
            self._flows.pop(flow_id, None)
            generation = self._generation
        completed = replace(browser, status="failed", error_code="authorization_denied" if error else None)
        if not error and code:
            try:
                token = self.client.exchange_code(oauth_flow, code)
                if self.verify_token is not None and not self.verify_token(token):
                    raise OAuthError("authenticated profile verification failed")
                with self._lock:
                    if generation != self._generation:
                        return replace(browser, status="failed", error_code="flow_cancelled")
                    self.token_manager.save(token)
                    if self.on_connected is not None:
                        self.on_connected()
                    completed = replace(browser, status="connected", error_code=None)
                    self._completed[flow_id] = (completed, oauth_flow)
                    return completed
            except Exception:
                # Do not surface provider payloads or token details to the browser/model.
                with self._lock:
                    if generation != self._generation:
                        return replace(browser, status="failed", error_code="flow_cancelled")
                    self.token_manager.clear()
                    completed = replace(browser, status="failed", error_code="account_verification_failed")
        elif not error:
            completed = replace(browser, status="failed", error_code="missing_authorization_code")
        with self._lock:
            if generation != self._generation:
                return replace(browser, status="failed", error_code="flow_cancelled")
            self._completed[flow_id] = (completed, oauth_flow)
        return completed

    def viewer_flow(self, flow_id: str, capability: str) -> BrowserFlow:
        return self._get(flow_id, capability)[0]

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self.states.clear()
            self._flows.clear()
            self._completed.clear()
