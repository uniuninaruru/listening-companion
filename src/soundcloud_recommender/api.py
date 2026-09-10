"""Read-only SoundCloud API adapter with bounded, in-memory responses."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode, urljoin, urlsplit

from .cache import TTLCache
from .config import Config
from .errors import AllowlistError, AuthenticationRequired, ProviderError, ValidationError
from .http import HttpResponse, HttpTransport, response_error_message
from .models import Collection, ProviderItem, normalize_collection, normalize_item
from .oauth import OAuthClient, OAuthToken, TokenManager
from .security import EndpointAllowlist, canonical_resource_reference, validate_identifier, validate_soundcloud_permalink


class SoundCloudApi:
    """GET-only adapter. Provider records stay in memory and never go to MCP directly."""

    def __init__(
        self,
        config: Config,
        transport: HttpTransport,
        token_manager: TokenManager,
        oauth_client: OAuthClient,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.token_manager = token_manager
        self.oauth_client = oauth_client
        self.clock = clock or time.time
        self.allowlist = EndpointAllowlist(config.api_base_url, config.authorize_url, config.token_url)
        self._provider_cache: TTLCache[object] = TTLCache(config.cache_ttl_seconds, self.clock)
        self._app_token: OAuthToken | None = None
        self._app_token_lock = threading.RLock()

    def _path_url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if not path.startswith("/") or "\\" in path or ".." in path.split("?", 1)[0].split("/"):
            raise ValidationError("API path is invalid")
        url = self.allowlist.build_api_url(path.split("?", 1)[0])
        if params:
            encoded: list[tuple[str, str]] = []
            for key, value in params.items():
                if value is None:
                    continue
                encoded.append((str(key), str(value).lower() if isinstance(value, bool) else str(value)))
            if encoded:
                url += "?" + urlencode(encoded)
        return url

    def _public_token(self) -> OAuthToken:
        with self._app_token_lock:
            if self._app_token and not self._app_token.needs_refresh(self.clock(), 60):
                return self._app_token
            if not self.config.has_client_credentials:
                raise AuthenticationRequired("public search requires local SoundCloud client credentials")
            try:
                self._app_token = self.oauth_client.client_credentials()
            except Exception as exc:
                raise AuthenticationRequired("public search authorization is unavailable") from exc
            return self._app_token

    def _token(self, *, user_required: bool, public_allowed: bool = False) -> OAuthToken:
        if user_required:
            token_value = self.token_manager.get_access_token()
            token = self.token_manager.load()
            if token is None or token.access_token != token_value:
                # The manager can rotate between load/get; the string is the only
                # secret needed for the request, so construct a short-lived view.
                return OAuthToken(access_token=token_value, expires_at=self.clock() + 120, refresh_token=None)
            return token
        if self.token_manager.connected:
            token_value = self.token_manager.get_access_token()
            token = self.token_manager.load()
            return token or OAuthToken(access_token=token_value, expires_at=self.clock() + 120)
        if public_allowed:
            return self._public_token()
        raise AuthenticationRequired("SoundCloud account is not connected")

    def _request(self, url: str, token: OAuthToken, *, allow_redirect: bool = False) -> HttpResponse:
        self.allowlist.assert_api_url(url)
        response = self.transport.request(
            "GET",
            url,
            headers={"Accept": "application/json; charset=utf-8", "Authorization": f"OAuth {token.access_token}"},
            timeout=self.config.request_timeout_seconds,
        )
        if 300 <= response.status < 400 and not allow_redirect:
            raise ProviderError(response_error_message(response))
        if response.status < 200 or response.status >= 300:
            if allow_redirect and 300 <= response.status < 400:
                return response
            raise ProviderError(response_error_message(response))
        return response

    def _json(self, url: str, *, user_required: bool, public_allowed: bool = False, allow_redirect: bool = False) -> object:
        cache_key = f"{user_required}:{public_allowed}:{url}"
        cached = self._provider_cache.get(cache_key)
        if cached is not None:
            return cached
        token = self._token(user_required=user_required, public_allowed=public_allowed)
        response = self._request(url, token, allow_redirect=allow_redirect)
        payload = response.json()
        self._provider_cache.put(cache_key, payload)
        return payload

    def _collection(
        self,
        path: str,
        *,
        kind: str,
        limit: int = 25,
        params: Mapping[str, Any] | None = None,
        cursor_url: str | None = None,
        user_required: bool = True,
        public_allowed: bool = False,
        linked_partitioning: bool = True,
    ) -> Collection:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError("limit must be between 1 and 50")
        if cursor_url:
            self.allowlist.assert_api_url(cursor_url)
            url = cursor_url
        else:
            request_params = dict(params or {})
            request_params["limit"] = limit
            if linked_partitioning:
                request_params["linked_partitioning"] = "true"
            url = self._path_url(path, request_params)
        payload = self._json(url, user_required=user_required, public_allowed=public_allowed)
        try:
            collection = normalize_collection(payload, kind)
        except ValueError as exc:
            raise ProviderError("provider collection response was invalid") from exc
        return Collection(collection.items[:limit], collection.next_href)

    def me_with_token(self, token: OAuthToken) -> ProviderItem:
        # Verification uses the newly exchanged token before it is stored.
        response = self._request(self._path_url("/me"), token)
        try:
            return normalize_item(response.json(), "user")
        except ValueError as exc:
            raise ProviderError("profile response was invalid") from exc

    def profile(self) -> ProviderItem:
        url = self._path_url("/me")
        token = self._token(user_required=True)
        try:
            return normalize_item(self._request(url, token).json(), "user")
        except ValueError as exc:
            raise ProviderError("profile response was invalid") from exc

    def recent_plays(self) -> Collection:
        # Current API behavior: this endpoint accepts access only. Do not add a
        # limit or linked_partitioning parameter and never infer older history.
        payload = self._json(self._path_url("/me/recently-played/tracks"), user_required=True)
        try:
            collection = normalize_collection(payload, "track")
        except ValueError as exc:
            raise ProviderError("recent plays response was invalid") from exc
        return Collection(collection.items[:25], None)

    def liked_tracks(self, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._collection("/me/likes/tracks", kind="track", limit=limit, cursor_url=cursor_url)

    def liked_playlists(self, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._collection("/me/likes/playlists", kind="playlist", limit=limit, cursor_url=cursor_url)

    def my_playlists(self, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._collection(
            "/me/playlists", kind="playlist", limit=limit, cursor_url=cursor_url, params={"show_tracks": "false"}
        )

    def followings(self, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._collection("/me/followings", kind="user", limit=limit, cursor_url=cursor_url)

    def following_tracks(self, limit: int = 25, offset: int = 0) -> Collection:
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise ValidationError("offset must be between 0 and 10000")
        # This endpoint uses limit+offset; linked_partitioning is intentionally absent.
        return self._collection(
            "/me/followings/tracks",
            kind="track",
            limit=limit,
            params={"offset": offset},
            linked_partitioning=False,
        )

    def search_tracks(self, query: str, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._search("/tracks", "track", query, limit, cursor_url)

    def search_playlists(self, query: str, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._search("/playlists", "playlist", query, limit, cursor_url)

    def search_users(self, query: str, limit: int = 25, cursor_url: str | None = None) -> Collection:
        return self._search("/users", "user", query, limit, cursor_url)

    def _search(self, path: str, kind: str, query: str, limit: int, cursor_url: str | None) -> Collection:
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 120 or any(ord(ch) < 32 for ch in query):
            raise ValidationError("search query must contain 1-120 printable characters")
        return self._collection(
            path,
            kind=kind,
            limit=limit,
            cursor_url=cursor_url,
            params={"q": query.strip()},
            user_required=False,
            public_allowed=True,
        )

    @staticmethod
    def _resource_path(kind: str, reference: str, suffix: str = "") -> str:
        canonical = canonical_resource_reference(reference, kind)
        plural = {"track": "tracks", "playlist": "playlists", "user": "users"}[kind]
        return f"/{plural}/{quote(canonical, safe=':')}" + suffix

    def resolve_resource(self, url: str) -> ProviderItem:
        validate_soundcloud_permalink(url)
        endpoint = self._path_url("/resolve", {"url": url})
        token = self._token(user_required=False, public_allowed=True)
        response = self._request(endpoint, token, allow_redirect=True)
        if 300 <= response.status < 400:
            location = response.header("location")
            if not location:
                raise ProviderError("resolve redirect did not include a target")
            target = urljoin(endpoint, location)
            # Validate the API origin before following the provider redirect.
            self.allowlist.assert_api_url(target)
            response = self._request(target, token)
        try:
            return normalize_item(response.json())
        except ValueError as exc:
            raise ProviderError("resolve response was invalid") from exc

    def get_track(self, reference: str) -> ProviderItem:
        payload = self._json(self._path_url(self._resource_path("track", reference)), user_required=False, public_allowed=True)
        try:
            return normalize_item(payload, "track")
        except ValueError as exc:
            raise ProviderError("track response was invalid") from exc

    def get_playlist(self, reference: str) -> ProviderItem:
        payload = self._json(
            self._path_url(self._resource_path("playlist", reference), {"show_tracks": "false"}),
            user_required=False,
            public_allowed=True,
        )
        try:
            return normalize_item(payload, "playlist")
        except ValueError as exc:
            raise ProviderError("playlist response was invalid") from exc

    def playlist_tracks(self, reference: str, limit: int = 50, cursor_url: str | None = None) -> Collection:
        return self._collection(
            self._resource_path("playlist", reference, "/tracks"),
            kind="track",
            limit=limit,
            cursor_url=cursor_url,
            user_required=False,
            public_allowed=True,
        )

    def related_tracks(self, reference: str, limit: int = 25, cursor_url: str | None = None) -> Collection:
        canonical = canonical_resource_reference(reference, "track")
        return self._collection(
            f"/tracks/{quote(canonical, safe=':')}/related",
            kind="track",
            limit=limit,
            cursor_url=cursor_url,
            user_required=False,
            public_allowed=True,
        )

    def clear_cache(self) -> None:
        self._provider_cache.clear()
        with self._app_token_lock:
            self._app_token = None
