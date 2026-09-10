"""Metadata-only podcast/episode heuristic."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .models import ProviderItem


_PODCAST_RE = re.compile(
    r"\b(podcast|episode|ep\.?\s*\d+|season\s*\d+|interview|conversation|talk show|daily|news|lecture|radio show)\b|#\s*\d{1,4}",
    re.IGNORECASE,
)
_MIX_RE = re.compile(r"\b(dj mix|mix|continuous mix|live set|dj set|remix|bootleg|club set)\b", re.IGNORECASE)


def classify_item(item: ProviderItem) -> dict[str, Any]:
    text = item.searchable_text()
    title_description = " ".join(part for part in (item.title, item.description) if part)
    explicit = bool(_PODCAST_RE.search(title_description))
    metadata_signal = bool(_PODCAST_RE.search(" ".join((item.genre, *item.tags))))
    mix_signal = bool(_MIX_RE.search(text))
    if item.kind == "playlist":
        child_podcast_count = sum(1 for child in item.children if classify_item(child)["is_podcast"])
        playlist_signal = bool(item.title and _PODCAST_RE.search(item.title)) or (
            bool(item.children) and child_podcast_count >= max(1, len(item.children) // 2)
        )
    else:
        playlist_signal = False

    if explicit or playlist_signal:
        confidence = 0.92 if explicit else 0.78
        is_podcast = True
    elif metadata_signal and not mix_signal:
        confidence = 0.68
        is_podcast = True
    else:
        confidence = 0.12 if mix_signal else 0.18
        is_podcast = False

    signals: list[str] = []
    if explicit:
        signals.append("episode-or-podcast-language")
    if metadata_signal:
        signals.append("podcast-metadata")
    if playlist_signal:
        signals.append("podcast-playlist-pattern")
    if mix_signal:
        signals.append("mix-or-set-language")
    return {
        "kind": item.kind,
        "item_id": item.item_id,
        "is_podcast": is_podcast,
        "confidence": round(confidence, 3),
        "signals": signals,
    }


def classify_items(items: Iterable[ProviderItem]) -> list[dict[str, Any]]:
    return [classify_item(item) for item in items]
