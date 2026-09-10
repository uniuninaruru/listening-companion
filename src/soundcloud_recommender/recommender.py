"""Deterministic, local recommendation scoring."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from .classifier import classify_item
from .models import ProviderItem, Recommendation


_WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)


def _terms(profile: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("genres", "podcast_topics", "languages", "preferred_creators"):
        for value in profile.get(key, []) if isinstance(profile.get(key, []), list) else []:
            if isinstance(value, str):
                values.extend(_WORD_RE.findall(value.lower()))
    return sorted(set(values))


class RecommendationEngine:
    SOURCE_WEIGHTS = {
        "recent_plays": 5.0,
        "liked_tracks": 4.0,
        "liked_playlists": 3.5,
        "my_playlists": 3.0,
        "followings": 2.5,
        "following_tracks": 2.5,
        "search_tracks": 1.8,
        "search_playlists": 1.6,
        "related_tracks": 1.5,
    }

    def rank(
        self,
        sources: Mapping[str, Iterable[ProviderItem]],
        profile: Mapping[str, Any] | None = None,
        *,
        limit: int = 10,
        include_podcasts: bool = True,
        content_type: str = "mixed",
        known_keys: set[tuple[str, str]] | None = None,
        new_only: bool = True,
        allowed_kinds: set[str] | None = None,
    ) -> list[Recommendation]:
        if not 1 <= limit <= 50:
            raise ValueError("recommendation limit must be between 1 and 50")
        if content_type not in {"music", "podcast", "mixed"}:
            raise ValueError("content_type must be music, podcast, or mixed")
        profile = profile or {}
        preferred = set(_terms(profile))
        avoid = {
            token
            for value in profile.get("avoid_terms", []) if isinstance(profile.get("avoid_terms", []), list)
            for token in _WORD_RE.findall(str(value).lower())
        }
        preferred_duration = profile.get("preferred_duration_minutes")
        discovery_level = profile.get("discovery_level", "balanced")
        known_keys = known_keys or set()
        allowed_kinds = allowed_kinds or {"track", "playlist"}

        # History and likes are local signals for discovering NEW records. They
        # are never returned to the caller through the live MCP boundary.
        history_terms: set[str] = set()
        history_creators: set[str] = set()
        for signal_source in ("recent_plays", "liked_tracks", "liked_playlists", "my_playlists"):
            for signal_item in sources.get(signal_source, ()):
                history_terms.update(_WORD_RE.findall(signal_item.searchable_text()))
                if signal_item.creator:
                    history_creators.add(signal_item.creator.lower())
        candidates: dict[tuple[str, str], tuple[ProviderItem, float, set[str], int]] = {}
        for source_name, source_items in sources.items():
            weight = self.SOURCE_WEIGHTS.get(source_name, 1.0)
            for position, item in enumerate(source_items):
                key = (item.kind, item.item_id)
                if item.kind not in allowed_kinds or (new_only and key in known_keys):
                    continue
                text = item.searchable_text()
                access = str(item.metadata.get("access") or "").lower()
                sharing = str(item.metadata.get("sharing") or "").lower()
                if access in {"blocked", "private"} or sharing == "private" or item.metadata.get("private") is True:
                    continue
                if avoid and any(term in text for term in avoid):
                    continue
                classification = classify_item(item)
                is_podcast = bool(classification["is_podcast"])
                if content_type == "podcast" and not is_podcast:
                    continue
                if content_type == "music" and is_podcast:
                    continue
                if not include_podcasts and is_podcast:
                    continue
                score = weight / (1.0 + (position * 0.08))
                reasons: set[str] = {f"source:{source_name}"}
                matched = sorted(term for term in preferred if term in text)
                if matched:
                    score += min(6.0, 1.5 * len(matched))
                    reasons.add("preference-match")
                history_matches = sorted(term for term in history_terms if term in text)
                if history_matches:
                    score += min(4.0, 0.8 * len(history_matches))
                    reasons.add("history-or-likes-similarity")
                if item.creator and item.creator.lower() in history_creators:
                    score += 1.0
                    reasons.add("familiar-creator")
                if classification["is_podcast"] and profile.get("podcast_topics"):
                    score += 1.5
                    reasons.add("podcast-profile")
                if item.kind == "track" and preferred_duration and item.duration_ms:
                    duration_minutes = item.duration_ms / 60000
                    distance = abs(duration_minutes - int(preferred_duration)) / max(int(preferred_duration), 1)
                    if distance <= 0.35:
                        score += 1.0
                        reasons.add("duration-match")
                    elif distance >= 1.5:
                        score -= 1.0
                if discovery_level == "high" and source_name.startswith(("search", "related")):
                    score += 0.7
                    reasons.add("discovery-mode")
                if discovery_level == "low" and source_name.startswith(("search", "related")):
                    score -= 0.5
                existing = candidates.get(key)
                if existing:
                    old_item, old_score, old_reasons, old_hits = existing
                    candidates[key] = (old_item, max(old_score, score) + min(1.5, score * 0.12), old_reasons | reasons, old_hits + 1)
                else:
                    candidates[key] = (item, score, reasons, 1)

        # Stable creator diversity is applied to every candidate before the
        # final truncation, so one creator cannot crowd out all other sources.
        creator_counts: defaultdict[str, int] = defaultdict(int)
        ordered = sorted(
            candidates.values(),
            key=lambda entry: (-entry[1], entry[0].kind, entry[0].item_id),
        )
        output: list[Recommendation] = []
        for item, score, reasons, _hits in ordered:
            creator = (item.user_id or item.creator or "").lower()
            if creator:
                score -= min(1.5, creator_counts[creator] * 0.35)
                creator_counts[creator] += 1
            final_reasons = tuple(sorted(reasons))
            output.append(Recommendation(item=item, score=score, reasons=final_reasons))
        # Diversity penalties are included after the first stable sort; restore a total order.
        output.sort(key=lambda rec: (-rec.score, rec.item.kind, rec.item.item_id))
        return output[:limit]
