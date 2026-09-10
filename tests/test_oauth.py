from __future__ import annotations

import threading

import pytest

from soundcloud_recommender.config import Config
from soundcloud_recommender.errors import AuthenticationRequired, OAuthError
from soundcloud_recommender.oauth import (
    OAuthClient,
    OAuthManager,
    OAuthStateManager,
    OAuthToken,
    SessionTokenStore,
    TokenManager,
)
from soundcloud_recommender.security import pkce_challenge

from .conftest import MockTransport, json_response


def test_pkce_and_state_are_bound_and_single_use() -> None:
    now = [100.0]
    manager = OAuthStateManager(ttl_seconds=600, clock=lambda: now[0])
    flow = manager.create(client_id="client", redirect_uri="http://127.0.0.1:8765/oauth/callback")
    assert pkce_challenge(flow.code_verifier) == flow.code_challenge
    consumed = manager.consume(flow.state, redirect_uri=flow.redirect_uri)
    assert consumed.state == flow.state
    with pytest.raises(OAuthError):
        manager.consume(flow.state, redirect_uri=flow.redirect_uri)

    expiring_flow = manager.create(client_id="client", redirect_uri=flow.redirect_uri)
    now[0] += 600
    with pytest.raises(OAuthError):
        manager.consume(expiring_flow.state, redirect_uri=flow.redirect_uri)


def test_authorization_uses_official_authorize_path_and_no_invented_scope() -> None:
    config = Config(client_id="client", client_secret="secret")
    client = OAuthClient(config, MockTransport(), clock=lambda: 100.0)
    flow = OAuthStateManager(clock=lambda: 100.0).create(client_id="client", redirect_uri=config.redirect_uri)
    url = client.authorization_url(flow)
    assert url.startswith("https://secure.soundcloud.com/authorize?")
    assert "scope=" not in url


def test_failed_refresh_clears_rotatable_token_without_replay() -> None:
    class RefreshClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def refresh(self, refresh_token: str) -> OAuthToken:
            self.calls.append(refresh_token)
            raise RuntimeError("mock refresh failure")

    store = SessionTokenStore()
    store.save(OAuthToken(access_token="old-access", refresh_token="one-use-refresh", expires_at=10.0))
    client = RefreshClient()
    manager = TokenManager(client, store, clock=lambda: 100.0)
    with pytest.raises(AuthenticationRequired):
        manager.get_access_token()
    assert client.calls == ["one-use-refresh"]
    assert store.load() is None
    with pytest.raises(AuthenticationRequired):
        manager.get_access_token()
    assert client.calls == ["one-use-refresh"]


def test_successful_refresh_requires_rotated_refresh_token() -> None:
    class RefreshClient:
        def refresh(self, refresh_token: str) -> OAuthToken:
            return OAuthToken(access_token="new-access", refresh_token=None, expires_at=1_000)

    store = SessionTokenStore()
    store.save(OAuthToken(access_token="old-access", refresh_token="one-use-refresh", expires_at=10.0))
    with pytest.raises(AuthenticationRequired):
        TokenManager(RefreshClient(), store, clock=lambda: 100.0).get_access_token()
    assert store.load() is None


def test_callback_success_is_fenced_when_disconnect_clears_generation() -> None:
    config = Config(client_id="client", client_secret="secret")
    store = SessionTokenStore()
    started = threading.Event()
    release = threading.Event()
    connected_callbacks: list[bool] = []

    class DelayedClient:
        def exchange_code(self, flow, code: str) -> OAuthToken:
            started.set()
            assert release.wait(2.0)
            return OAuthToken(access_token="new-access", refresh_token="new-refresh", expires_at=2_000)

    oauth = OAuthManager(
        config,
        DelayedClient(),
        TokenManager(DelayedClient(), store, clock=lambda: 100.0),
        verify_token=lambda token: True,
        on_connected=lambda: connected_callbacks.append(True),
        clock=lambda: 100.0,
    )
    browser = oauth.start()
    result: list[OAuthToken | object] = []

    def callback() -> None:
        try:
            result.append(oauth.complete_callback(state=browser.state, code="code"))
        except Exception as exc:  # pragma: no cover - the assertion below is the outcome
            result.append(exc)

    thread = threading.Thread(target=callback)
    thread.start()
    assert started.wait(2.0)
    oauth.clear()
    release.set()
    thread.join(2.0)

    assert not thread.is_alive()
    assert store.load() is None
    assert not connected_callbacks
    assert result and getattr(result[0], "error_code", None) == "flow_cancelled"
