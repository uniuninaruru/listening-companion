---
name: listening-companion
description: Use the local Listening Companion MCP service for private, read-only SoundCloud discovery, podcast classification, and deterministic recommendations.
---

# Listening Companion

Use this skill when the user asks for a SoundCloud listening lookup, podcast discovery, recent-play-based recommendations, or preference-profile management through the local Listening Companion service.

## User-facing rules

- Ask the user to run the local service and authorize through its loopback browser page when `connection_status` says the account is not connected. Never ask them to paste a client secret, access token, or refresh token into chat.
- Pass only a minimal, purpose-specific `preference_profile` from the current conversation: `genres`, `podcast_topics`, `languages`, `preferred_creators`, `avoid_terms`, `preferred_duration_minutes`, and `discovery_level`. Do not request or enumerate all ChatGPT memories, and do not pass a raw transcript.
- Treat a live tool response as a receipt, not a catalog. It contains an app-owned `result_id`, count, status, warnings, expiry, and local `viewer_url`. Tell the user to open the viewer URL to inspect the attributed provider content.
- Do not restate, summarize, quote, or copy live provider titles, descriptions, artists, or SoundCloud URLs into the chat. Synthetic demo results are explicitly labeled and may be shown for testing.
- Explain partial-source warnings honestly. A missing likes/following/search source must not be presented as a complete history.
- Recommendations are deterministic and metadata-heuristic. Do not claim the service listened to audio, transcribed speech, or used an embedding/model.
- Saving preferences requires an explicit user request and `consent: true`. A one-off profile is not saved automatically.
- `disconnect_account` clears local tokens and in-memory provider data. It does not assert remote revocation.

## Tool routing

1. Call `connection_status` before a private operation.
2. Use `search_*`, `recent_plays`, `liked_*`, `my_playlists`, `followings`, and `following_tracks` to create expiring local viewer receipts.
3. Use `resolve_resource`, `get_track`, `get_playlist`, `playlist_tracks`, and `related_tracks` only with bounded URLs/IDs and direct the user to the resulting viewer receipt.
4. Use `classify_podcasts` with a prior live `result_id` so provider metadata stays inside the service; use full item inputs only for explicitly synthetic/demo data.
5. Use `recommend` with a minimal current profile. It combines sources locally and reports partial-source warnings.
6. Use `get_preferences`, `save_preferences`, and `delete_preferences` only for the user's explicit preference-profile request.
