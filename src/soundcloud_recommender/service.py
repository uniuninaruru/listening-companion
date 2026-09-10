"""Transport-independent Listening Companion operations and privacy boundary."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Mapping

from .api import SoundCloudApi
from .cache import ResultCache, TTLCache
from .classifier import classify_item
from .config import Config
from .errors import (
    AuthenticationRequired,
    CacheMiss,
    ConfigurationError,
    ListeningCompanionError,
    ProviderError,
    ValidationError,
)
from .http import HttpTransport, UrllibTransport
from .models import Collection, ProviderItem, Recommendation, normalize_item
from .oauth import OAuthManager, OAuthToken, OAuthClient, SessionTokenStore, TokenManager, TokenStore
from .preferences import PreferenceStore, validate_preference_profile
from .recommender import RecommendationEngine
from .security import random_opaque_id, validate_identifier


class ListeningService:
    """Application service used by both MCP and the local browser viewer."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        transport: HttpTransport | None = None,
        token_store: TokenStore | None = None,
        clock: Callable[[], float] | None = None,
        demo_mode: bool = False,
    ) -> None:
        self.config = config or Config.from_env()
        self.clock = clock or time.time
        self.transport = transport or UrllibTransport()
        self.token_store = token_store or SessionTokenStore()
        self.oauth_client = OAuthClient(self.config, self.transport, self.clock)
        self.token_manager = TokenManager(self.oauth_client, self.token_store, self.clock)
        self.viewer_base_url = self._viewer_base_url()
        self.result_cache = ResultCache(self.config.cache_ttl_seconds, self.viewer_base_url, self.clock)
        self.cursor_cache: TTLCache[str] = TTLCache(self.config.cache_ttl_seconds, self.clock, max_entries=128)
        self.preference_store = PreferenceStore(self.config.preferences_db or Path("preferences.sqlite3"))
        self.engine = RecommendationEngine()
        self.demo_mode = demo_mode
        self.api = SoundCloudApi(
            self.config,
            self.transport,
            self.token_manager,
            self.oauth_client,
            clock=self.clock,
        )
        self.oauth = OAuthManager(
            self.config,
            self.oauth_client,
            self.token_manager,
            verify_token=self._verify_token,
            on_connected=self._clear_provider_state,
            clock=self.clock,
        )

    def _viewer_base_url(self) -> str:
        host = self.config.viewer_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.config.viewer_port}"

    def _verify_token(self, token: OAuthToken) -> bool:
        try:
            self.api.me_with_token(token)
            return True
        except ListeningCompanionError:
            return False

    def _clear_provider_state(self) -> None:
        self.api.clear_cache()
        self.result_cache.clear()
        self.cursor_cache.clear()

    def connection_status(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        token = self.token_manager.load()
        if token is None:
            status = "configuration_required" if not self.config.has_client_credentials else "disconnected"
            return {
                "status": status,
                "connected": False,
                "storage": self.token_manager.storage_kind,
                "warnings": [],
            }
        return {
            "status": "connected",
            "connected": True,
            "storage": self.token_manager.storage_kind,
            "expires_in_seconds": max(0, int(token.expires_at - self.clock())),
            "warnings": [],
        }

    def connect_account(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        if not self.config.has_client_credentials:
            return {
                "status": "configuration_required",
                "connected": False,
                "result_id": random_opaque_id(18),
                "count": 0,
                "viewer_url": f"{self.viewer_base_url}/setup",
                "expires_in_seconds": 0,
                "warnings": [{"code": "credentials_missing", "message": "local client credentials are not configured"}],
            }
        browser = self.oauth.start()
        return {
            "status": "authorization_required",
            "connected": False,
            "result_id": browser.flow_id,
            "count": 0,
            "viewer_url": f"{self.viewer_base_url}/auth/start/{browser.flow_id}?token={browser.capability}",
            "expires_in_seconds": max(0, int(browser.expires_at - self.clock())),
            "warnings": [],
        }

    def disconnect_account(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        # Increment the OAuth generation before clearing token state so an
        # in-flight callback cannot restore an account after this operation.
        self.oauth.clear()
        self.token_manager.clear()
        self._clear_provider_state()
        return {
            "status": "disconnected",
            "connected": False,
            "local_tokens_deleted": True,
            "in_memory_provider_state_cleared": True,
            "warnings": [],
        }

    def _reject_keys(self, args: Mapping[str, Any], allowed: set[str]) -> None:
        if not isinstance(args, Mapping):
            raise ValidationError("tool arguments must be an object")
        unknown = set(args) - allowed
        if unknown:
            raise ValidationError("tool arguments contain unsupported fields")

    @staticmethod
    def _limit(args: Mapping[str, Any], default: int = 25) -> int:
        value = args.get("limit", default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
            raise ValidationError("limit must be between 1 and 50")
        return value

    def _cursor_url(self, args: Mapping[str, Any]) -> str | None:
        cursor = args.get("cursor")
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not 16 <= len(cursor) <= 200:
            raise ValidationError("cursor is invalid")
        next_href = self.cursor_cache.pop(cursor)
        if not next_href:
            raise CacheMiss("cursor is expired or unknown")
        self.api.allowlist.assert_api_url(next_href)
        return next_href

    def _warning(self, exc: Exception) -> dict[str, str]:
        if isinstance(exc, AuthenticationRequired):
            return {"code": "authentication_required", "message": "this source needs a connected account"}
        if isinstance(exc, ConfigurationError):
            return {"code": "configuration_required", "message": "this source is not configured"}
        if isinstance(exc, CacheMiss):
            return {"code": "cursor_expired", "message": "a prior page cursor expired"}
        if isinstance(exc, ProviderError):
            return {"code": "source_unavailable", "message": "this source was unavailable"}
        return {"code": "source_failed", "message": "this source could not be used"}

    def _expose(self, operation: str, data: Iterable[Any], warnings: list[dict[str, str]] | None = None, next_href: str | None = None) -> dict[str, Any]:
        warning_tuple = tuple(warnings or [])
        result = self.result_cache.store(operation, tuple(data), warning_tuple)
        next_cursor = self.cursor_cache.put_cursor(next_href) if next_href else None
        return self.result_cache.public_summary(result, next_cursor=next_cursor, status="partial" if warning_tuple else "ok")

    def _expose_collection(self, operation: str, collection: Collection) -> dict[str, Any]:
        next_cursor = self.cursor_cache.put_cursor(collection.next_href) if collection.next_href else None
        result = self.result_cache.store(operation, collection.items)
        return self.result_cache.public_summary(result, next_cursor=next_cursor)

    def _list_operation(self, operation: str, args: Mapping[str, Any], fetch: Callable[..., Collection]) -> dict[str, Any]:
        limit = self._limit(args)
        cursor = self._cursor_url(args)
        collection = fetch(limit=limit, cursor_url=cursor)
        return self._expose_collection(operation, collection)

    def recent_plays(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        return self._expose_collection("recent_plays", self.api.recent_plays())

    def liked_tracks(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"limit", "cursor"})
        return self._list_operation("liked_tracks", args, self.api.liked_tracks)

    def liked_playlists(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"limit", "cursor"})
        return self._list_operation("liked_playlists", args, self.api.liked_playlists)

    def my_playlists(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"limit", "cursor"})
        return self._list_operation("my_playlists", args, self.api.my_playlists)

    def followings(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"limit", "cursor"})
        return self._list_operation("followings", args, self.api.followings)

    def following_tracks(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"limit", "offset"})
        limit = self._limit(args)
        offset = args.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
            raise ValidationError("offset must be between 0 and 10000")
        return self._expose_collection("following_tracks", self.api.following_tracks(limit=limit, offset=offset))

    def _search(self, operation: str, args: Mapping[str, Any], fetch: Callable[..., Collection]) -> dict[str, Any]:
        self._reject_keys(args, {"query", "limit", "cursor"})
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("query is required")
        limit = self._limit(args)
        cursor = self._cursor_url(args)
        return self._expose_collection(operation, fetch(query=query.strip(), limit=limit, cursor_url=cursor))

    def search_tracks(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._search("search_tracks", args, self.api.search_tracks)

    def search_playlists(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._search("search_playlists", args, self.api.search_playlists)

    def search_users(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return self._search("search_users", args, self.api.search_users)

    def get_profile(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        return self._expose("get_profile", (self.api.profile(),))

    def resolve_resource(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"url"})
        url = args.get("url")
        if not isinstance(url, str):
            raise ValidationError("url is required")
        return self._expose("resolve_resource", (self.api.resolve_resource(url),))

    def get_track(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"reference"})
        ref = args.get("reference")
        if not isinstance(ref, str):
            raise ValidationError("reference is required")
        return self._expose("get_track", (self.api.get_track(ref),))

    def get_playlist(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"reference"})
        ref = args.get("reference")
        if not isinstance(ref, str):
            raise ValidationError("reference is required")
        return self._expose("get_playlist", (self.api.get_playlist(ref),))

    def playlist_tracks(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"reference", "limit", "cursor"})
        ref = args.get("reference")
        if not isinstance(ref, str):
            raise ValidationError("reference is required")
        cursor = self._cursor_url(args)
        return self._expose_collection("playlist_tracks", self.api.playlist_tracks(ref, self._limit(args), cursor))

    def related_tracks(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"reference", "limit", "cursor"})
        ref = args.get("reference")
        if not isinstance(ref, str):
            raise ValidationError("reference is required")
        cursor = self._cursor_url(args)
        return self._expose_collection("related_tracks", self.api.related_tracks(ref, self._limit(args), cursor))

    def classify_podcasts(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"result_id", "items", "synthetic"})
        result_id = args.get("result_id")
        items_arg = args.get("items")
        synthetic = args.get("synthetic", False)
        if result_id is not None:
            if not isinstance(result_id, str):
                raise ValidationError("result_id is invalid")
            cached = self.result_cache.internal_get(result_id)
            provider_items = tuple(
                record.item if isinstance(record, Recommendation) else record
                for record in cached.data
                if isinstance(record, (ProviderItem, Recommendation))
            )
            enriched = tuple(
                item.with_podcast(
                    classify_item(item)["confidence"] if classify_item(item)["is_podcast"] else 0.0,
                    classify_item(item)["signals"],
                )
                for item in provider_items
            )
            result = self.result_cache.store("classify_podcasts", enriched)
            return self.result_cache.public_summary(result)
        if synthetic is not True or not isinstance(items_arg, list) or not 1 <= len(items_arg) <= 100:
            raise ValidationError("classify_podcasts needs a prior result_id or synthetic fixture items with synthetic=true")
        normalized: list[ProviderItem] = []
        for item in items_arg:
            if not isinstance(item, Mapping):
                raise ValidationError("synthetic items must be objects")
            try:
                normalized.append(normalize_item(item))
            except ValueError as exc:
                raise ValidationError("synthetic item is invalid") from exc
        output = []
        for item in normalized:
            output.append({**item.viewer_dict(), "classification": classify_item(item), "demo": True})
        return {"status": "demo", "count": len(output), "items": output, "warnings": []}

    def recommend(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"preference_profile", "limit", "content_type"})
        profile_arg = args.get("preference_profile")
        profile = validate_preference_profile(profile_arg) if profile_arg is not None else (self.preference_store.get() or {})
        limit = args.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValidationError("limit must be between 1 and 20")
        content_type = args.get("content_type", "mixed")
        if content_type not in {"music", "podcast", "mixed"}:
            raise ValidationError("content_type must be music, podcast, or mixed")

        sources: dict[str, tuple[ProviderItem, ...]] = {}
        warnings: list[dict[str, str]] = []

        def add(name: str, fetch: Callable[[], Collection]) -> None:
            try:
                sources[name] = fetch().items
            except (AuthenticationRequired, ConfigurationError, ProviderError) as exc:
                warnings.append(self._warning(exc))

        if self.demo_mode:
            demo = _demo_items()
            sources = {
                "recent_plays": (demo[0],),
                "liked_tracks": (demo[0],),
                "liked_playlists": (demo[2],),
                "my_playlists": (demo[2],),
                "followings": (),
                "following_tracks": (demo[0],),
                "search_tracks": (demo[3], demo[1]),
                "search_playlists": (demo[4],),
                "related_tracks": (demo[3],),
            }
        else:
            add("recent_plays", self.api.recent_plays)
            add("liked_tracks", lambda: self.api.liked_tracks(50))
            add("liked_playlists", lambda: self.api.liked_playlists(25))
            add("my_playlists", lambda: self.api.my_playlists(25))
            add("followings", lambda: self.api.followings(25))
            add("following_tracks", lambda: self.api.following_tracks(50, 0))

            query_values: list[str] = []
            for key in ("genres", "podcast_topics", "preferred_creators"):
                values = profile.get(key, [])
                if isinstance(values, list):
                    query_values.extend(str(value) for value in values if str(value).strip())
            if content_type == "podcast" and not query_values:
                query_values.append("podcast")
            if content_type == "music" and not query_values:
                query_values.append("music")
            for query in query_values[:3]:
                try:
                    collection = self.api.search_tracks(query, 25)
                    sources["search_tracks"] = sources.get("search_tracks", ()) + collection.items
                except (AuthenticationRequired, ConfigurationError, ProviderError) as exc:
                    warnings.append(self._warning(exc))
                if content_type in {"podcast", "mixed"}:
                    try:
                        collection = self.api.search_playlists(query, 20)
                        sources["search_playlists"] = sources.get("search_playlists", ()) + collection.items
                    except (AuthenticationRequired, ConfigurationError, ProviderError) as exc:
                        warnings.append(self._warning(exc))

            recent = sources.get("recent_plays", ())
            for item in recent[:3]:
                try:
                    related = self.api.related_tracks(item.urn or item.item_id, 20)
                    sources["related_tracks"] = sources.get("related_tracks", ()) + related.items
                except (AuthenticationRequired, ConfigurationError, ProviderError) as exc:
                    warnings.append(self._warning(exc))

        known: set[tuple[str, str]] = set()
        for name in ("recent_plays", "liked_tracks", "liked_playlists", "my_playlists"):
            for item in sources.get(name, ()):
                known.add(item.key)
                known.update(child.key for child in item.children)
        try:
            ranked = self.engine.rank(
                sources,
                profile,
                limit=limit,
                content_type=content_type,
                known_keys=known,
                new_only=True,
                allowed_kinds={"track", "playlist"},
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        result = self.result_cache.store("recommend", tuple(ranked), tuple(warnings))
        return self.result_cache.public_summary(result, status="partial" if warnings else "ok")

    def get_preferences(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        profile = self.preference_store.get()
        return {"status": "saved" if profile is not None else "empty", "preference_profile": profile or {}, "warnings": []}

    def save_preferences(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"preference_profile", "consent"})
        profile = args.get("preference_profile")
        if not isinstance(profile, Mapping):
            raise ValidationError("preference_profile is required")
        normalized = self.preference_store.save(profile, consent=args.get("consent") is True)
        return {"status": "saved", "saved": True, "field_count": len(normalized), "warnings": []}

    def delete_preferences(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, set())
        self.preference_store.delete()
        return {"status": "deleted", "saved": False, "warnings": []}

    def demo_catalog(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._reject_keys(args, {"content_type"})
        content_type = args.get("content_type", "mixed")
        if content_type not in {"music", "podcast", "mixed"}:
            raise ValidationError("content_type must be music, podcast, or mixed")
        items = list(_demo_items())
        if content_type == "podcast":
            items = [item for item in items if classify_item(item)["is_podcast"]]
        elif content_type == "music":
            items = [item for item in items if not classify_item(item)["is_podcast"]]
        return {
            "status": "demo",
            "count": len(items),
            "items": [{**item.viewer_dict(), "classification": classify_item(item), "demo": True} for item in items],
            "warnings": [],
        }

    def call(self, name: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        arguments: Mapping[str, Any] = {} if args is None else args
        method = getattr(self, name, None)
        if method is None or not callable(method) or name.startswith("_"):
            raise ValidationError("unknown tool")
        return method(arguments)

    def render_cached_result(self, result_id: str, capability: str) -> tuple[str, list[dict[str, Any]], tuple[dict[str, str], ...]]:
        cached = self.result_cache.authorize_view(result_id, capability)
        rows: list[dict[str, Any]] = []
        for record in cached.data:
            if hasattr(record, "viewer_dict"):
                rows.append(record.viewer_dict())
            elif isinstance(record, Mapping):
                rows.append(dict(record))
        return cached.operation, rows, cached.warnings


def _demo_items() -> tuple[ProviderItem, ...]:
    return (
        ProviderItem(
            kind="track", item_id="demo-music-1", urn="soundcloud:tracks:demo-music-1",
            title="Night Drive Demo", description="Synthetic electronic music fixture", creator="Demo Artist",
            user_id="demo-artist", provider_url="https://soundcloud.com/example/night-drive-demo",
            duration_ms=240000, genre="Electronic", tags=("ambient", "synth"), metadata={"access": "playable", "sharing": "public"},
        ),
        ProviderItem(
            kind="track", item_id="demo-podcast-1", urn="soundcloud:tracks:demo-podcast-1",
            title="Synthetic Podcast Episode 12", description="Synthetic interview fixture", creator="Demo Host",
            user_id="demo-host", provider_url="https://soundcloud.com/example/synthetic-podcast-episode-12",
            duration_ms=3600000, genre="Podcast", tags=("interview", "episode"), metadata={"access": "playable", "sharing": "public"},
        ),
        ProviderItem(
            kind="track", item_id="demo-mix-1", urn="soundcloud:tracks:demo-mix-1",
            title="Synthetic DJ Mix", description="Synthetic mix fixture", creator="Demo DJ",
            user_id="demo-dj", provider_url="https://soundcloud.com/example/synthetic-dj-mix",
            duration_ms=4200000, genre="House", tags=("DJ mix",), metadata={"access": "playable", "sharing": "public"},
        ),
        ProviderItem(
            kind="track", item_id="demo-new-music", urn="soundcloud:tracks:demo-new-music",
            title="Synthetic Ambient Discovery", description="Synthetic new music fixture", creator="Demo Artist",
            user_id="demo-artist", provider_url="https://soundcloud.com/example/synthetic-ambient-discovery",
            duration_ms=260000, genre="Electronic", tags=("ambient", "synth"), metadata={"access": "playable", "sharing": "public"},
        ),
        ProviderItem(
            kind="playlist", item_id="demo-new-podcast-playlist", urn="soundcloud:playlists:demo-new-podcast-playlist",
            title="Synthetic Podcast Discovery", description="Synthetic podcast playlist fixture", creator="Demo Network",
            user_id="demo-network", provider_url="https://soundcloud.com/example/synthetic-podcast-discovery",
            genre="Podcast", tags=("podcast", "interview"), metadata={"access": "playable", "sharing": "public"},
        ),
    )
