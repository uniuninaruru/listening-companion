"""Bounded normalized records held only in the local in-memory session."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

_SC_HOSTS = {"soundcloud.com", "www.soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"}
_ALLOWED_KINDS = frozenset({"track", "playlist", "user"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9:_-]{1,180}$")


def bounded_text(value: Any, max_length: int = 12000) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).replace("\x00", "").strip()[:max_length]


def _safe_provider_url(value: Any) -> str:
    candidate = bounded_text(value, 2000)
    if not candidate:
        return ""
    parsed = urlsplit(candidate)
    if parsed.scheme != "https" or parsed.hostname not in _SC_HOSTS:
        return ""
    if parsed.username or parsed.password or parsed.fragment:
        return ""
    return candidate


def _string_list(value: Any, max_items: int = 30) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return ()
    result: list[str] = []
    for item in values:
        text = bounded_text(item, 120)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return tuple(result)


def _user(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    user = payload.get("user") if isinstance(payload.get("user"), Mapping) else {}
    return (
        bounded_text(user.get("id") or payload.get("user_id"), 100),
        bounded_text(user.get("username") or user.get("full_name") or payload.get("username"), 240),
        _safe_provider_url(user.get("permalink_url") or payload.get("user_permalink_url")),
    )


@dataclass(frozen=True, slots=True)
class ProviderItem:
    kind: str
    item_id: str
    urn: str = ""
    title: str = ""
    description: str = ""
    creator: str = ""
    user_id: str = ""
    provider_url: str = ""
    duration_ms: int | None = None
    genre: str = ""
    tags: tuple[str, ...] = ()
    created_at: str = ""
    published_at: str = ""
    track_count: int | None = None
    children: tuple["ProviderItem", ...] = ()
    podcast_score: float = 0.0
    podcast_reasons: tuple[str, ...] = ()
    recommendation_score: float = 0.0
    recommendation_reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError("unsupported provider item kind")
        if not self.item_id or not _IDENTIFIER_RE.fullmatch(self.item_id):
            raise ValueError("provider item ID is invalid")

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.item_id

    def searchable_text(self) -> str:
        return " ".join(
            part for part in (self.title, self.description, self.creator, self.genre, *self.tags) if part
        ).lower()

    def with_podcast(self, score: float, reasons: Iterable[str]) -> "ProviderItem":
        return _replace_item(self, podcast_score=round(float(score), 4), podcast_reasons=tuple(reasons))

    def with_recommendation(self, score: float, reasons: Iterable[str]) -> "ProviderItem":
        return _replace_item(
            self, recommendation_score=round(float(score), 4), recommendation_reasons=tuple(reasons)
        )

    def viewer_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "id": self.item_id,
            "urn": self.urn,
            "title": self.title,
            "description": self.description,
            "creator": self.creator,
            "permalink_url": self.provider_url,
            "duration_ms": self.duration_ms,
            "genre": self.genre,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "published_at": self.published_at,
        }
        if self.track_count is not None:
            data["track_count"] = self.track_count
        if self.children:
            data["tracks"] = [child.viewer_dict() for child in self.children]
        if self.podcast_score:
            data["podcast_score"] = self.podcast_score
            data["podcast_reasons"] = list(self.podcast_reasons)
        if self.recommendation_score:
            data["recommendation_score"] = self.recommendation_score
            data["recommendation_reasons"] = list(self.recommendation_reasons)
        return data


def _replace_item(item: ProviderItem, **changes: Any) -> ProviderItem:
    values = {
        "kind": item.kind, "item_id": item.item_id, "urn": item.urn, "title": item.title,
        "description": item.description, "creator": item.creator, "user_id": item.user_id,
        "provider_url": item.provider_url, "duration_ms": item.duration_ms, "genre": item.genre,
        "tags": item.tags, "created_at": item.created_at, "published_at": item.published_at,
        "track_count": item.track_count, "children": item.children, "podcast_score": item.podcast_score,
        "podcast_reasons": item.podcast_reasons, "recommendation_score": item.recommendation_score,
        "recommendation_reasons": item.recommendation_reasons, "metadata": item.metadata,
    }
    values.update(changes)
    return ProviderItem(**values)


def normalize_item(payload: Mapping[str, Any], kind_hint: str | None = None) -> ProviderItem:
    if not isinstance(payload, Mapping):
        raise ValueError("provider item must be an object")
    nested_kind = kind_hint
    # Track and playlist wrappers are documented envelope shapes. A normal
    # track/playlist also contains a nested ``user`` uploader object, so never
    # unwrap that object merely because the key is present.
    for kind in ("track", "playlist"):
        nested = payload.get(kind)
        if isinstance(nested, Mapping):
            payload = nested
            nested_kind = kind
            break
    if nested_kind is None or nested_kind == "user":
        nested_user = payload.get("user")
        resource_fields = {
            "id",
            "title",
            "name",
            "description",
            "permalink_url",
            "permalink",
            "username",
            "full_name",
            "urn",
            "uri",
            "duration",
            "genre",
            "tag_list",
            "tags",
            "tracks",
            "track_count",
            "playlist_type",
            "created_at",
            "release_date",
            "published_at",
            "access",
            "sharing",
            "private",
        }
        # A user envelope is accepted only when the outer object has no
        # resource-shaped fields of its own. This preserves uploader.user on
        # ordinary provider records while still handling {"user": {...}}.
        if isinstance(nested_user, Mapping) and not (set(payload) & resource_fields):
            payload = nested_user
            nested_kind = "user"
    kind = nested_kind or bounded_text(payload.get("kind"), 40).lower()
    if kind == "set":
        kind = "playlist"
    if kind not in _ALLOWED_KINDS:
        if "username" in payload and "title" not in payload:
            kind = "user"
        elif "tracks" in payload or "track_count" in payload or "playlist_type" in payload:
            kind = "playlist"
        else:
            kind = "track"
    raw_id = payload.get("id")
    item_id = bounded_text(raw_id, 100)
    urn = bounded_text(payload.get("urn") or payload.get("uri"), 180)
    if not item_id and urn:
        item_id = urn.rsplit(":", 1)[-1]
    provider_url = _safe_provider_url(payload.get("permalink_url") or payload.get("permalink"))
    if not item_id and provider_url:
        item_id = "url-" + hashlib.sha256(provider_url.encode("utf-8")).hexdigest()[:24]
    if not item_id or not _IDENTIFIER_RE.fullmatch(item_id):
        raise ValueError("provider item has no safe stable ID")
    user_id, creator, user_url = _user(payload)
    title = bounded_text(payload.get("title") or payload.get("name") or payload.get("full_name") or payload.get("username"), 2000)
    duration_raw = payload.get("duration")
    try:
        duration = int(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None
    track_count_raw = payload.get("track_count")
    try:
        track_count = int(track_count_raw) if track_count_raw is not None else None
    except (TypeError, ValueError):
        track_count = None
    children: list[ProviderItem] = []
    if kind == "playlist" and isinstance(payload.get("tracks"), list):
        for child in payload["tracks"][:200]:
            if isinstance(child, Mapping):
                try:
                    children.append(normalize_item(child, "track"))
                except ValueError:
                    continue
    return ProviderItem(
        kind=kind,
        item_id=item_id,
        urn=urn,
        title=title or f"{kind.title()} {item_id}",
        description=bounded_text(payload.get("description")),
        creator=creator,
        user_id=user_id,
        provider_url=provider_url or user_url,
        duration_ms=duration,
        genre=bounded_text(payload.get("genre"), 240),
        tags=_string_list(payload.get("tag_list") or payload.get("tags")),
        created_at=bounded_text(payload.get("created_at"), 80),
        published_at=bounded_text(payload.get("release_date") or payload.get("published_at"), 80),
        track_count=track_count,
        children=tuple(children),
        metadata={
            "likes_count": payload.get("likes_count"),
            "playback_count": payload.get("playback_count"),
            "access": payload.get("access"),
            "sharing": payload.get("sharing"),
            "private": payload.get("private"),
        },
    )


@dataclass(frozen=True, slots=True)
class Collection:
    items: tuple[ProviderItem, ...]
    next_href: str | None = None


def normalize_collection(payload: Any, kind_hint: str | None = None) -> Collection:
    if isinstance(payload, Mapping) and isinstance(payload.get("collection"), list):
        raw_items = payload["collection"]
        next_href = bounded_text(payload.get("next_href"), 2000) or None
    elif isinstance(payload, list):
        raw_items = payload
        next_href = None
    elif isinstance(payload, Mapping):
        raw_items = [payload]
        next_href = None
    else:
        raise ValueError("provider response is not a collection")
    items: list[ProviderItem] = []
    for raw in raw_items[:200]:
        if isinstance(raw, Mapping):
            try:
                items.append(normalize_item(raw, kind_hint))
            except ValueError:
                continue
    return Collection(tuple(items), next_href)


@dataclass(frozen=True, slots=True)
class Recommendation:
    item: ProviderItem
    score: float
    reasons: tuple[str, ...]

    def viewer_dict(self) -> dict[str, Any]:
        return {
            **self.item.viewer_dict(),
            "recommendation_score": round(self.score, 4),
            "recommendation_reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class CachedResult:
    result_id: str
    capability: str = field(repr=False)
    operation: str = ""
    data: tuple[Any, ...] = ()
    created_at: float = 0.0
    expires_at: float = 0.0
    warnings: tuple[dict[str, str], ...] = ()
