from __future__ import annotations

from soundcloud_recommender.models import ProviderItem
from soundcloud_recommender.recommender import RecommendationEngine


def item(
    item_id: str,
    title: str,
    *,
    creator: str = "Creator",
    kind: str = "track",
    metadata: dict[str, object] | None = None,
) -> ProviderItem:
    return ProviderItem(
        kind=kind,
        item_id=item_id,
        title=title,
        creator=creator,
        user_id=creator.lower(),
        genre="ambient",
        metadata=metadata or {},
    )


def test_history_and_likes_are_signals_for_new_items_and_known_items_are_excluded() -> None:
    history = item("history", "Ambient Focus", creator="A")
    liked = item("liked", "Deep Ambient", creator="A")
    new_a = item("new-a", "Ambient Drift", creator="B")
    new_b = item("new-b", "Ambient Horizon", creator="C")
    private = item("private", "Ambient Private", metadata={"private": True, "sharing": "private"})
    blocked = item("blocked", "Ambient Blocked", metadata={"access": "blocked"})

    ranked = RecommendationEngine().rank(
        {
            "recent_plays": [history],
            "liked_tracks": [liked],
            "search_tracks": [private, blocked, new_a, new_b],
        },
        {"genres": ["ambient"]},
        limit=50,
        known_keys={history.key, liked.key},
        new_only=True,
    )
    keys = {recommendation.item.key for recommendation in ranked}
    assert new_a.key in keys
    assert new_b.key in keys
    assert history.key not in keys
    assert liked.key not in keys
    assert private.key not in keys
    assert blocked.key not in keys
    assert all("history-or-likes-similarity" in recommendation.reasons for recommendation in ranked)


def test_podcast_filter_and_creator_diversity_apply_before_limit() -> None:
    podcast = item("podcast", "Podcast Episode 8", creator="Podcaster")
    music = item("music", "Ambient Track", creator="Musician")
    same_creator_one = item("same-1", "Ambient One", creator="Popular")
    same_creator_two = item("same-2", "Ambient Two", creator="Popular")
    another_creator = item("other", "Ambient Three", creator="Other")

    podcast_ranked = RecommendationEngine().rank(
        {"search_tracks": [podcast, music]},
        {},
        limit=5,
        content_type="podcast",
        known_keys=set(),
    )
    assert [recommendation.item.key for recommendation in podcast_ranked] == [podcast.key]

    diverse = RecommendationEngine().rank(
        {"search_tracks": [same_creator_one, same_creator_two, another_creator]},
        {},
        limit=2,
        known_keys=set(),
    )
    assert len(diverse) == 2
    assert len({recommendation.item.creator for recommendation in diverse}) == 2
