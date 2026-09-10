# Listening Companion

Listening Companion is a single-user, local Python MVP for read-only discovery over a connected SoundCloud account. It is packaged as a Codex plugin with a dependency-free MCP JSON-RPC stdio server and a loopback browser viewer.

The app display name is intentionally **Listening Companion**. The internal package/plugin slug remains `soundcloud-recommender` for compatibility; it is not the user-facing product name.

## What is implemented

- OAuth 2.1 authorization-code flow with required S256 PKCE, a single-use state bound to the exact redirect URI, ten-minute state expiry, replay protection, and refresh-token rotation.
- Access and refresh tokens are session-only by default: they live in the running process and are cleared on disconnect or exit. No plaintext token file is supported by the MVP, and tokens are never returned by MCP tools or logged.
- An allowlisted, GET-only SoundCloud API client for `/me`, recently played tracks, likes, playlists, followings/following tracks, search, resolve, resource details, playlist tracks, and related tracks.
- A loopback viewer that holds provider records only in an in-memory, expiring result cache. The viewer HTML is escaped and includes links back to SoundCloud for attribution. No provider content is written to SQLite or to an archive.
- Deterministic, local, rule-based podcast classification and recommendations. There are no embedding calls, model calls, audio downloads, stream capture, or fine-tuning.
- An opt-in SQLite store for a bounded `preference_profile` only. It does not store raw conversations, memory enumeration, provider payloads, or listening history.
- A synthetic demo mode for end-to-end testing and first-run inspection without credentials or network access.

## Important live-output boundary

For live provider operations, the MCP result is deliberately limited to app-owned fields:

```json
{
  "status": "ok",
  "result_id": "opaque-random-id",
  "count": 5,
  "viewer_url": "http://127.0.0.1:8765/view/opaque-random-id?token=opaque-capability",
  "expires_in_seconds": 600,
  "warnings": []
}
```

Track titles, descriptions, artists, SoundCloud URLs, and provider payloads do **not** cross the live MCP/model boundary. They are rendered only by the authenticated local viewer for the person who opened the opaque capability URL. Synthetic demo fixtures may return full data to make local tests understandable.

The loopback viewer uses an opaque capability token, a short-lived in-memory result, `HttpOnly`/`SameSite=Strict` cookies, and no external bind by default. It is a local convenience, not a production identity system; do not expose it to a LAN or the public internet.

## Quick start without credentials

```bash
# Run these commands from the plugin root (the directory containing this file).
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python scripts/validate_local.py
python scripts/run_mcp.py --demo
```

The demo server speaks MCP over stdout and sends no logs to stdout, so it can be attached directly to an MCP host. NDJSON (newline-delimited JSON) is the default stdio framing. Hosts that require legacy Content-Length framing can select it with `--framing content-length`; it is not the default.

`.env.example` is a template only; the service does not load dotenv files automatically. For a local shell run, copy it to an ignored file, replace the placeholders, and export/source it before starting the server, for example:

```bash
cp .env.example .env.local
# Edit .env.local, then load it only into this shell:
set -a
. ./.env.local
set +a
python scripts/run_mcp.py
```

When a GUI host launches the stdio server, it receives the environment that existed when the host started. After adding or changing credentials, restart the host (or reload the plugin if that host explicitly supports environment reload) before calling `connect_account`.

## Live local setup

1. Register an API application through the current SoundCloud developer process. Current SoundCloud guidance says API application registration may require Artist Pro. Keep the client ID and security code in a private environment, never in this plugin or a chat message.
2. Copy `.env.example` into a private ignored file and export/source it in the shell that launches the service. Leave `SOUNDCLOUD_SCOPE` empty unless SoundCloud has explicitly supplied a supported scope for the app; this project invents no scopes. The example file is not auto-loaded.
3. Register the exact loopback redirect URI from `LISTENING_COMPANION_REDIRECT_URI` with the app.
4. Run `python scripts/run_mcp.py`. The service starts a loopback viewer on `127.0.0.1:8765` by default. Call `connect_account`; open the returned local `viewer_url`. The local page redirects the browser to SoundCloud and receives the callback. If a GUI host was already open before the credentials were exported, restart that host first.
5. Call the read-only tools. Open each returned `viewer_url` in the local browser to see provider metadata and attribution links.

The `connect_account` result may be `configuration_required` or `authorization_required`. That is an honest state: no account connection is attempted without valid local credentials, and this repository never activates an account on its own.

## MCP tools

The server exposes strict schemas for:

`connection_status`, `connect_account`, `disconnect_account`, `get_profile`, `recent_plays`, `liked_tracks`, `liked_playlists`, `my_playlists`, `followings`, `following_tracks`, `search_tracks`, `search_playlists`, `search_users`, `resolve_resource`, `get_track`, `get_playlist`, `playlist_tracks`, `related_tracks`, `classify_podcasts`, `recommend`, `get_preferences`, `save_preferences`, `delete_preferences`, and `demo_catalog`.

Provider lists use bounded pagination. Recently played is requested with the documented endpoint and is capped at 25 because that endpoint accepts access only in the current API behavior. Ordinary collections use `limit` and `linked_partitioning`; following tracks uses `limit` and `offset`. `next_href` is followed only after exact API-host allowlist validation. `/resolve` is treated as a possible 302 response and redirect targets are validated before any follow-up request. Related tracks use `/tracks/{urn}/related`.

`recommend` fetches sources independently and returns partial-source warnings when a source is unavailable. The profile may include only bounded fields such as genres, podcast topics, languages, preferred creators, avoid terms, preferred duration, and discovery level. A request profile is ephemeral unless the user explicitly calls `save_preferences` with `consent: true`.

## Tests and validation

```bash
python -m pytest
python scripts/validate_local.py
python <path-to-plugin-creator>/scripts/validate_plugin.py .
```

The tests use a mock HTTP transport and local temporary stores. They make no network calls and verify OAuth replay/expiry/PKCE, refresh rotation, allowlists and redirect handling, payload normalization, cache expiry, preference consent, deterministic ranking, podcast classification, MCP framing/schema errors, and the live output boundary.

## Terms, rights, and scope limits

This is an implementation scaffold, not a legal certification or an approval to operate a live integration. The SoundCloud API Terms of Use currently describe API responses as User Content, restrict using User Content as input to AI technologies, prohibit persistent User Content caching, require attribution/backlinks, require a privacy policy for personal data, and prohibit using SoundCloud Marks as the app name. This code therefore performs local rule-based processing, keeps live provider data in a session-only memory cache, and returns the model only an app-owned opaque local-viewer receipt; provider metadata and provider links remain in the authenticated local viewer. It uses the display name Listening Companion. Appropriate rights clearance and review of the current SoundCloud terms are still required before live operation.

The local MCP packaging format is intentionally conservative. `.mcp.json` uses a command/args/cwd/env entry plus `env_vars` for hosts that support local stdio MCP servers; host-specific installation, Python path handling, and remote HTTPS MCP authentication are not certified here. This is not a direct ChatGPT-Web remote integration and does not create a hosted website. A production remote MCP deployment would require separate hosting, authentication, data-protection review, and rights clearance. This project does not deploy anything.

## File layout

```text
src/soundcloud_recommender/
  cache.py          in-memory provider/result/cursor TTL
  classifier.py     metadata-only podcast heuristic
  cli.py            local command entry point
  config.py         environment configuration and limits
  http.py           allowlisted HTTP transport abstractions
  mcp_stdio.py      direct MCP JSON-RPC stdio server
  models.py         bounded domain and public result models
  oauth.py          PKCE, callback flow, token lifecycle
  preferences.py    opt-in preference_profile SQLite store
  recommender.py    deterministic local ranking
  service.py        operations and live-output boundary
  viewer.py         loopback authenticated result viewer
```
