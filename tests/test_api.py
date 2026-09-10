from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from soundcloud_recommender.api import SoundCloudApi
from soundcloud_recommender.config import Config
from soundcloud_recommender.errors import AllowlistError, ValidationError
from soundcloud_recommender.models import normalize_item
from soundcloud_recommender.oauth import OAuthClient, OAuthToken, SessionTokenStore, TokenManager

from .conftest import MockTransport, collection_payload, item_payload, json_response


def make_api(transport: MockTransport, *, connected: bool = True) -> SoundCloudApi:
    config = Config(client_id="client-id", client_secret="client-secret")
    store = SessionTokenStore()
    if connected:
        store.save(OAuthToken(access_token="user-access", refresh_token="refresh", expires_at=10_000))
    oauth_client = OAuthClient(config, transport, clock=lambda: 1_000)
    manager = TokenManager(oauth_client, store, clock=lambda: 1_000)
    return SoundCloudApi(config, transport, manager, oauth_client, clock=lambda: 1_000)


def generic_responder(**call):
    path = urlsplit(call["url"]).path
    if call["method"] == "GET" and path == "/tracks/soundcloud:tracks:123":
        return json_response(item_payload(123, title="Track 123"))
    if call["method"] == "GET" and path == "/playlists/soundcloud:playlists:42":
        return json_response(item_payload(42, title="Playlist 42", kind="playlist"))
    if call["method"] == "GET":
        return json_response(collection_payload([item_payload(1), item_payload(2)]))
    raise AssertionError(f"unexpected request: {call}")


def test_normal_track_with_nested_uploader_user_stays_a_track() -> None:
    item = normalize_item(item_payload(123, title="Track 123", creator="Uploader"), "track")
    assert item.kind == "track"
    assert item.item_id == "123"
    assert item.title == "Track 123"
    assert item.creator == "Uploader"


def test_recent_plays_uses_exact_endpoint_and_caps_to_25() -> None:
    transport = MockTransport()
    payload = collection_payload([item_payload(i, title=f"Track {i}") for i in range(30)])
    transport.add("GET", "https://api.soundcloud.com/me/recently-played/tracks", json_response(payload))
    collection = make_api(transport).recent_plays()

    assert len(collection.items) == 25
    assert transport.calls[0]["url"] == "https://api.soundcloud.com/me/recently-played/tracks"
    assert "?" not in transport.calls[0]["url"]


def test_collection_pagination_and_following_tracks_parameters() -> None:
    transport = MockTransport(generic_responder)
    api = make_api(transport)

    api.liked_tracks(7)
    api.following_tracks(7, 3)

    liked_query = parse_qs(urlsplit(transport.calls[0]["url"]).query)
    following_query = parse_qs(urlsplit(transport.calls[1]["url"]).query)
    assert liked_query == {"limit": ["7"], "linked_partitioning": ["true"]}
    assert following_query == {"offset": ["3"], "limit": ["7"]}
    assert "linked_partitioning" not in following_query


def test_typed_resource_references_are_canonical_and_kind_checked() -> None:
    transport = MockTransport(generic_responder)
    api = make_api(transport)

    api.get_track("123")
    api.get_playlist("soundcloud:playlists:42")
    api.related_tracks("soundcloud:tracks:123", 5)
    api.playlist_tracks("42", 5)

    paths = [urlsplit(call["url"]).path for call in transport.calls]
    assert paths[0] == "/tracks/soundcloud:tracks:123"
    assert paths[1].startswith("/playlists/soundcloud:playlists:42")
    assert paths[2] == "/tracks/soundcloud:tracks:123/related"
    assert paths[3] == "/playlists/soundcloud:playlists:42/tracks"
    assert all("urn:soundcloud" not in path for path in paths)
    with pytest.raises(ValidationError):
        api.get_track("soundcloud:playlists:42")
    with pytest.raises(ValidationError):
        api.related_tracks("soundcloud:playlists:42")


def test_resolve_validates_redirect_before_following() -> None:
    source = "https://soundcloud.com/example/track"
    transport = MockTransport()
    transport.add(
        "GET",
        "https://api.soundcloud.com/resolve?url=https%3A%2F%2Fsoundcloud.com%2Fexample%2Ftrack",
        json_response({}, status=302, headers={"Location": "https://evil.example/track"}),
    )
    with pytest.raises(AllowlistError):
        make_api(transport).resolve_resource(source)
    assert len(transport.calls) == 1


def test_resolve_follows_only_allowlisted_api_redirect() -> None:
    source = "https://soundcloud.com/example/track"
    transport = MockTransport()
    resolve_url = "https://api.soundcloud.com/resolve?url=https%3A%2F%2Fsoundcloud.com%2Fexample%2Ftrack"
    transport.add("GET", resolve_url, json_response({}, status=302, headers={"Location": "/tracks/123"}))
    transport.add("GET", "https://api.soundcloud.com/tracks/123", json_response(item_payload(123)))

    item = make_api(transport).resolve_resource(source)
    assert item.item_id == "123"
    assert transport.calls[1]["url"] == "https://api.soundcloud.com/tracks/123"


def test_public_search_uses_basic_auth_without_secret_in_url() -> None:
    transport = MockTransport()
    token_url = "https://secure.soundcloud.com/oauth/token"
    search_url = "https://api.soundcloud.com/tracks?q=jazz&limit=5&linked_partitioning=true"
    transport.add("POST", token_url, json_response({"access_token": "app-access", "expires_in": 3600}))
    transport.add("GET", search_url, json_response(collection_payload([item_payload(1)])))
    api = make_api(transport, connected=False)

    api.search_tracks(" jazz ", 5)
    token_call, search_call = transport.calls
    assert token_call["method"] == "POST"
    assert token_call["url"] == token_url
    assert token_call["headers"]["Authorization"].startswith("Basic ")
    assert b"client-secret" not in token_call["body"]
    assert "client-secret" not in token_call["url"]
    assert search_call["headers"]["Authorization"] == "OAuth app-access"
