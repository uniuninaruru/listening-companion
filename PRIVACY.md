# Listening Companion privacy and data boundary

This document describes the local MVP behavior. It is not legal advice and does not replace a product-specific privacy policy or a review of current SoundCloud terms and applicable law.

## Data that may be processed

After the user explicitly starts the local OAuth flow, the service may receive the authenticated user's profile, recent plays, likes, playlists, followings, and public search/resource responses from SoundCloud. Those records are held in process memory only while a request/session result is usable. They are used by local, deterministic string/rule scoring and by the loopback viewer.

The model-facing MCP response contains only app-owned status, counts, opaque result IDs, expiry information, warnings, and loopback viewer URLs. It does not contain provider titles, descriptions, artist names, provider URLs, IDs as human-readable metadata, raw JSON, or access/refresh tokens. The viewer is the place where a person can inspect provider metadata and follow an attributed SoundCloud link.

## What is not stored

- No raw ChatGPT conversation, memory enumeration, prompt transcript, or model-generated free-form memory is stored.
- No SoundCloud track, playlist, user, or history payload is stored in SQLite, JSON archives, logs, or the plugin folder.
- No listening-history archive is built. The recently-played endpoint is read as a bounded, current source and is not accumulated.
- No audio is downloaded, copied, fingerprinted, streamed, or made available offline.
- No embeddings, model calls, training, fine-tuning, or external recommendation service receives SoundCloud User Content.

## Optional preference storage

The user may explicitly save a bounded `preference_profile` by calling `save_preferences` with `consent: true`. The stored profile contains only structured preference fields such as genres, podcast topics, languages, preferred creators, avoid terms, a preferred duration, and a discovery level. It is not automatically saved from a conversation. It can be read or deleted with the preferences tools. The SQLite database is outside the plugin directory by default and should be protected as user-private data.

## Tokens and disconnect

Access and refresh tokens are session-only in this MVP. They are held by the running process, never written to a plaintext token file, never included in MCP content, and never written to normal logs. `disconnect_account` clears the in-memory token, provider/result/cursor state, and pending OAuth flows. Exiting the process also removes the token from memory. It does not claim that a remote provider session has been revoked; users should also revoke access through SoundCloud if required.

## Local viewer security

The viewer binds to loopback by default, uses an unguessable short-lived capability URL, sets an `HttpOnly`/`SameSite=Strict` cookie after capability validation, and escapes all provider text before HTML rendering. It is intended for the same user on the same machine. Do not bind it to `0.0.0.0`, proxy it publicly, or reuse its URLs as durable public links.

## Provider and legal limits

SoundCloud's [API Terms of Use](https://developers.soundcloud.com/docs/api/terms-of-use) state that API content is User Content, restrict its use as an input to AI technologies, prohibit persistent provider-content caching, and require attribution and backlinks when displaying content. The implementation adopts a conservative app/model exposure boundary, but that does not certify live use. Review the current terms, obtain any needed rights/permissions, publish an appropriate privacy policy, and confirm host-specific OAuth/MCP requirements before connecting a real account.

This repository contains a local stdio service and loopback viewer only. It is not a direct ChatGPT-Web remote integration, does not host a public website, and does not deploy an OAuth callback service. The MIT license permits reuse under its terms, but it does not grant SoundCloud rights, API approval, or permission to use provider content beyond applicable agreements.
